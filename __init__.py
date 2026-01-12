from typing import Dict, List
from pathlib import PureWindowsPath, PurePosixPath

from binaryninja.binaryview import BinaryView, DataVariable
from binaryninja.log import Logger
from binaryninja.plugin import PluginCommand

# Note that this is a sample plugin and you may need to manually edit it with
# additional functionality. In particular, this example only passes in the
# binary view. If you would like to act on an addres or function you should
# consider using other register_for* functions.

# Add documentation about UI plugin alternatives and potentially getting
# current_* functions


def main(bv):
    logger = Logger(session_id=0, logger_name=__name__)

    def find_string_slice_variables_containing_source_file_path(
        bv: BinaryView
    ) -> List[DataVariable]:
        source_file_path_data_vars = []
        # TODO: Make this independent of the rust_string_slicer plugin
        for rust_string_slice_data_ref in bv.get_data_refs_for_type("RustStringSlice"):
            rust_string_slice_data = bv.get_data_var_at(rust_string_slice_data_ref)
            if rust_string_slice_data is not None:
                string_address = rust_string_slice_data.value.get("address")
                if string_address is not None:
                    string_data = bv.get_data_var_at(string_address)
                    if string_data is not None:
                        # TODO: This assumes that the string here is already the correct length
                        candidate_path = string_data.value.decode("utf-8")
                        if "windows" in bv.platform.name:
                            candidate_path = PureWindowsPath(candidate_path)
                        else:
                            candidate_path = PurePosixPath(candidate_path)
                        if candidate_path.suffix == ".rs":
                            source_file_path_data_vars.append(rust_string_slice_data)

        return source_file_path_data_vars

    # TODO: This relies on you having defined and figured out the layout of core::panic::Location first
    def set_panic_locations_from_source_file_path_string_variables(
        bv: BinaryView, source_file_paths: List[DataVariable]
    ) -> List[DataVariable]:
        panic_location_data_vars = []
        panic_location_type = bv.get_type_by_name("core::panic::Location")
        if panic_location_type is not None:
            for source_file_path_data_variable in source_file_paths:
                panic_location_data_var = bv.define_user_data_var(
                    addr=source_file_path_data_variable.address,
                    var_type=panic_location_type,
                    name=f"panic_location_{source_file_path_data_variable.name}",
                )
                logger.log_info(
                    f"Defined core::panic::location struct at {source_file_path_data_variable.address:#x}"
                )
                panic_location_data_vars.append(panic_location_data_var)

        return panic_location_data_vars

    def find_panic_location_code_refs_and_set_tags(
        bv: BinaryView, panic_locations: List[DataVariable]
    ):
        for panic_location_data_variable in panic_locations:
            panic_location_file_path_string_address = (
                panic_location_data_variable.value["file"]["address"]
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
