from dataclasses import dataclass
from typing import Dict, List
from pathlib import PureWindowsPath, PurePosixPath

from binaryninja.binaryview import BinaryView, DataVariable
from binaryninja.log import Logger
from binaryninja.plugin import PluginCommand
from binaryninja.types import (
    StructureBuilder,
    IntegerType,
    PointerType,
    Type,
    NamedTypeReferenceType,
)

logger = Logger(session_id=0, logger_name=__name__)


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


def main(bv):
    logger = Logger(session_id=0, logger_name=__name__)

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

    CorePanicLocation.create_binary_ninja_type(bv)

    bv.begin_undo_actions()

    source_file_paths = find_string_slice_variables_containing_source_file_path(bv)
    panic_locations = set_panic_locations_from_source_file_path_string_variables(
        bv, source_file_paths
    )
    find_panic_location_code_refs_and_set_tags(bv, panic_locations)

    bv.commit_undo_actions()
    bv.update_analysis()


PluginCommand.register(
    "find_panic_paths", "Find Rust panic location source paths", main
)
