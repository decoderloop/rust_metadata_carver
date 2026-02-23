from dataclasses import dataclass
from typing import Dict, List
from pathlib import PureWindowsPath, PurePosixPath

from binaryninja.binaryview import (
    BinaryView,
    DataVariable,
    Section,
    SectionSemantics,
    Segment,
)
from binaryninja.log import Logger
from binaryninja.plugin import BackgroundTaskThread, PluginCommand
from binaryninja.types import (
    StructureBuilder,
    IntegerType,
    PointerType,
    Type,
    NamedTypeReferenceType,
)

logger = Logger(session_id=0, logger_name=__name__)


class RustStringSlice:
    """
    Class to work with the string slice type in Rust, &str
    """

    @classmethod
    def check_binary_ninja_type_exists(cls, bv: BinaryView) -> bool:
        return bv.get_type_by_name("&str") is not None

    @classmethod
    def create_binary_ninja_type(cls, bv: BinaryView):
        if bv.arch is not None:
            rust_string_slice_bn_type_obj = StructureBuilder.create(packed=True)
            rust_string_slice_bn_type_obj.append(
                type=PointerType.create(arch=bv.arch, type=Type.char()), name="_address"
            )
            rust_string_slice_bn_type_obj.append(
                type=IntegerType.create(width=bv.arch.address_size), name="_length"
            )

            bv.define_user_type(
                name="&str",
                type_obj=rust_string_slice_bn_type_obj,
            )
            logger.log_info(f"Defined new type, `&str`, for Rust string slices")

    @classmethod
    def create_binary_ninja_instance(cls, bv: BinaryView, location: int, name: str):
        bv.define_user_data_var(addr=location, var_type="`&str`", name=name)
        logger.log_info(f"Defined new `&str` at {location:#x}")


@dataclass
class CorePanicLocation:
    """
    Class to work with the core::panic::Location type in the Rust core library
    (https://doc.rust-lang.org/beta/core/panic/struct.Location.html)

    This assumes that the layout of the core::panic::Location
    struct is always the following (pseudo-C, packed)
    representation:


    ```
    struct core::panic::Location
    {
        `&str` file;
        uint32_t line;
        uint32_t col;
    };
    ```

    While in theory this layout could change and is dependent
    on the Rust compiler's whims, in practice, from observing
    lots of Rust binaries, it is always this layout.
    """

    @classmethod
    def check_binary_ninja_type_exists(cls, bv: BinaryView) -> bool:
        return bv.get_type_by_name("core::panic::Location") is not None

    @classmethod
    def create_binary_ninja_type(cls, bv: BinaryView):
        if bv.arch is not None:
            rust_core_panic_location_bn_type_obj = StructureBuilder.create(packed=True)

            rust_core_panic_location_bn_type_obj.append(
                type=NamedTypeReferenceType.create_from_registered_type(bv, "&str"),
                name="file",
            )

            rust_core_panic_location_bn_type_obj.append(
                type=IntegerType.create(width=4), name="line"
            )
            rust_core_panic_location_bn_type_obj.append(
                type=IntegerType.create(width=4), name="col"
            )

            bv.define_user_type(
                name="core::panic::Location",
                type_obj=rust_core_panic_location_bn_type_obj,
            )
            logger.log_info(
                f"Defined new type, `core::panic::Location`, for Rust panic metadata"
            )

    @classmethod
    def create_binary_ninja_instance(
        cls, bv: BinaryView, location: int, name: str
    ) -> DataVariable | None:
        data_variable = bv.define_user_data_var(
            addr=location, var_type="`core::panic::Location`", name=name
        )
        logger.log_info(f"Defined new `core::panic::Location` at {location:#x}")

        if data_variable is None:
            logger.log_error(
                "Unable to create `core::panic::Location` data variable at {location:#x}"
            )

        return data_variable


class RecoverPanicPathStringsFromReadOnlyDataTask(BackgroundTaskThread):
    def __init__(self, bv: BinaryView):
        super().__init__(
            initial_progress_text="Recovering Rust panic path metadata strings from readonly data...",
            can_cancel=True,
        )
        self.bv = bv

    def run(self):
        if self.bv.arch is None:
            logger.log_error(
                "Could not get architecture of current binary view, exiting"
            )
            return

        readonly_segments: List[Segment] = list(
            filter(
                lambda segment: segment.readable
                and not segment.writable
                and not segment.executable,
                self.bv.segments,
            )
        )

        readonly_sections: List[Section] = list(
            filter(
                lambda section: section.semantics
                == SectionSemantics.ReadOnlyDataSectionSemantics,
                self.bv.sections.values(),
            )
        )

        if len(readonly_segments) == 0 and len(readonly_sections) == 0:
            logger.log_error(
                "Could not find any read-only segments or sections in binary, exiting"
            )
            return

        self.bv.begin_undo_actions()

        # Obtain all data vars which are pointers to data in read-only data segments or sections
        data_vars_to_readonly_data: List[DataVariable] = []
        for (
            _data_var_addr,
            candidate_string_slice_data_ptr,
        ) in self.bv.data_vars.items():
            if isinstance(candidate_string_slice_data_ptr.type, PointerType):
                for readonly_segment_or_section in (
                    readonly_segments + readonly_sections
                ):
                    if (
                        candidate_string_slice_data_ptr.value
                        in readonly_segment_or_section
                    ):
                        data_vars_to_readonly_data.append(
                            candidate_string_slice_data_ptr
                        )
                        logger.log_debug(
                            f"Found pointer var at {candidate_string_slice_data_ptr.address:#x} ({candidate_string_slice_data_ptr}) pointing to {candidate_string_slice_data_ptr.value:#x} "
                        )

        for candidate_string_slice_data_ptr in data_vars_to_readonly_data:
            # Try to read an integer following the data var,
            # and treat it as a candidate for a string slice length.
            candidate_string_slice_len_addr = (
                candidate_string_slice_data_ptr.address
                + candidate_string_slice_data_ptr.type.width
            )

            # Filter out anything at the candidate address
            # that's already defined as any data var type which is not an integer.
            existing_data_var_at_candidate_string_slice_len_addr = (
                self.bv.get_data_var_at(candidate_string_slice_len_addr)
            )
            if existing_data_var_at_candidate_string_slice_len_addr is not None:
                if not isinstance(
                    existing_data_var_at_candidate_string_slice_len_addr.type,
                    IntegerType,
                ):
                    continue

            candidate_string_slice_len = self.bv.read_int(
                address=candidate_string_slice_len_addr,
                size=self.bv.arch.address_size,  # In Rust's definition of the `str` type, this length is a `usize`, which is defined to be the same size as the size of pointers for the platform.
                sign=False,
                endian=self.bv.arch.endianness,
            )

            logger.log_debug(
                f"Pointer var at {candidate_string_slice_data_ptr.address:#x} is followed by integer with value {candidate_string_slice_len:#x}"
            )

            # Filter out any potential string slice which has length 0
            if candidate_string_slice_len == 0:
                continue
            # Filter out any potential string slice which is too long
            if candidate_string_slice_len >= 0x1000:  # TODO: maybe change this limit
                continue

            # Attempt to read out the pointed to value as a string slice, with the length obtained above.
            try:
                candidate_string_slice = self.bv.read(
                    addr=candidate_string_slice_data_ptr.value,
                    length=candidate_string_slice_len,
                )
            except Exception as err:
                logger.log_error(
                    f"Failed to read from address {candidate_string_slice_data_ptr.value} with length {candidate_string_slice_len}: {err}"
                )
                continue

            logger.log_debug(
                f"Obtained candidate string slice with addr {candidate_string_slice_data_ptr.value:#x}, len {candidate_string_slice_len:#x}: {candidate_string_slice}"
            )

            # Sanity check whether the recovered string is valid UTF-8
            try:
                candidate_utf8_string = candidate_string_slice.decode("utf-8")
                logger.log_info(
                    f'Recovered string at addr {candidate_string_slice_data_ptr.value:#x}, len {candidate_string_slice_len:#x}: "{candidate_utf8_string}"'
                )

                # Set the char[<candidate_string_slice_len>] type on the location pointed to by the data var.
                existing_string_slice_data = self.bv.get_data_var_at(
                    candidate_string_slice_data_ptr.value
                )
                if existing_string_slice_data is not None:
                    self.bv.undefine_user_data_var(
                        addr=candidate_string_slice_data_ptr.value
                    )

                self.bv.define_user_data_var(
                    addr=candidate_string_slice_data_ptr.value,
                    var_type=Type.array(
                        type=Type.char(), count=candidate_string_slice_len
                    ),
                )

                # Set the RustStringSlice type on the location of the data var.
                RustStringSlice.create_binary_ninja_instance(
                    bv=self.bv,
                    location=candidate_string_slice_data_ptr.address,
                    name=f'str_"{candidate_utf8_string}"',
                )

            except UnicodeDecodeError as err:
                logger.log_warn(
                    f"Candidate string slice {candidate_string_slice} does not decode to a valid UTF-8 string; excluding from final results: {err}"
                )
                continue

        self.bv.commit_undo_actions()
        self.bv.update_analysis()


def recover_panic_location_metadata_structure(bv: BinaryView):

    def find_string_slice_variables_containing_source_file_path(
        bv: BinaryView,
    ) -> List[DataVariable]:
        source_file_path_data_vars = []
        # TODO: Make this independent of the rust_string_slicer plugin
        for rust_string_slice_data_ref in bv.get_data_refs_for_type("&str"):
            rust_string_slice_data = bv.get_data_var_at(rust_string_slice_data_ref)
            if rust_string_slice_data is not None:
                string_address = rust_string_slice_data.value.get("_address")
                if string_address is not None:
                    string_data = bv.get_data_var_at(string_address)
                    if string_data is not None and string_data.value is not None:
                        # TODO: This assumes that the string here is already the correct length

                        if isinstance(string_data.value, bytes):
                            candidate_path = string_data.value.decode("utf-8")
                        elif isinstance(string_data.value, str):
                            candidate_path = string_data.value

                        if "windows" in bv.platform.name:
                            candidate_path = PureWindowsPath(candidate_path)
                        else:
                            candidate_path = PurePosixPath(candidate_path)
                        if candidate_path.suffix == ".rs":
                            source_file_path_data_vars.append(rust_string_slice_data)

        return source_file_path_data_vars

    def set_panic_locations_from_source_file_path_string_variables(
        bv: BinaryView, source_file_paths: List[DataVariable]
    ) -> List[DataVariable]:
        panic_location_data_vars = []

        if CorePanicLocation.check_binary_ninja_type_exists(bv):
            for source_file_path_data_variable in source_file_paths:
                panic_location_data_var = (
                    CorePanicLocation.create_binary_ninja_instance(
                        bv=bv,
                        location=source_file_path_data_variable.address,
                        name=f"panic_location_{source_file_path_data_variable.name}",
                    )
                )

                if panic_location_data_var is not None:
                    panic_location_data_vars.append(panic_location_data_var)

        return panic_location_data_vars

    def find_panic_location_code_refs_and_set_tags(
        bv: BinaryView, panic_locations: List[DataVariable]
    ):
        for panic_location_data_variable in panic_locations:
            panic_location_file_path_string_address = (
                panic_location_data_variable.value["file"]["_address"]
            )
            panic_location_file_path_string_data = bv.get_data_var_at(
                panic_location_file_path_string_address
            )
            if panic_location_file_path_string_data is not None:
                panic_location_path = panic_location_file_path_string_data.value.decode(
                    "utf-8"
                )
                panic_location_line = panic_location_data_variable.value["line"]
                panic_location_col = panic_location_data_variable.value["col"]

                panic_location_tag_type_name = (
                    f"{panic_location_path} - Rust Panic Location Source File Path"
                )
                bv.create_tag_type(panic_location_tag_type_name, "😱")

                panic_location_code_refs = bv.get_code_refs(
                    panic_location_data_variable.address
                )
                for panic_location_code_ref in panic_location_code_refs:
                    code_ref_address = panic_location_code_ref.address
                    bv.add_tag(
                        addr=code_ref_address,
                        tag_type_name=panic_location_tag_type_name,
                        data=f"{panic_location_path}: line {panic_location_line}, col {panic_location_col}",
                        user=True,
                    )
                    logger.log_info(
                        f"Added tag {panic_location_path} at {code_ref_address}"
                    )

    if not CorePanicLocation.check_binary_ninja_type_exists(bv):
        CorePanicLocation.create_binary_ninja_type(bv)

    bv.begin_undo_actions()

    source_file_paths = find_string_slice_variables_containing_source_file_path(bv)
    panic_locations = set_panic_locations_from_source_file_path_string_variables(
        bv, source_file_paths
    )
    find_panic_location_code_refs_and_set_tags(bv, panic_locations)

    bv.commit_undo_actions()
    bv.update_analysis()


def find_panic_location_paths(bv: BinaryView):
    if not RustStringSlice.check_binary_ninja_type_exists(bv):
        RustStringSlice.create_binary_ninja_type(bv)
        RecoverPanicPathStringsFromReadOnlyDataTask(bv=bv).start()


PluginCommand.register(
    "Rust Metadata Carver\\Find Panic Location Paths",
    "Find Rust panic location source paths",
    find_panic_location_paths,
)

PluginCommand.register(
    "Rust Metadata Carver\\Recover Panic Location Metadata Structures",
    "Recover panic location metadata structures",
    recover_panic_location_metadata_structure,
)
