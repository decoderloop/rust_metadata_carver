# Rust Metadata Carver
Find metadata in Rust binaries

![A screenshot of the Tags interface in Binary Ninja, showing a large number of tags labelled with the source file path, source file line, and source file column of panic location metadata embedded inside a Rust binary. For example, one tag has the label "library\std\src\sys\windows\c.rs: line 1362, col 9". All tags are using the 😱 emoji as an icon.](images/panic-path-tags-screenshot-border.png)

![A screenshot of several Rust "core::panic::Location" structs, all with the source file path "library\std\src\sys\windows\stdio.rs" and each with a line number and column number.](images/panic-location-structs-screenshot-border.png)

## Description

Plugin to find metadata from Rust binaries, including:

- Source file locations from panic unwind metadata (i.e. `core::panic::Location` structs embedded in the binary) 

## Minimum Version

4689

## License

This plugin is released under an [MIT license](./LICENSE).

## Metadata Version

2

## Development

### Setting up a development environment

To set up a development environment, including setting up a Python virtual environment:

```
python -m venv .venv && . .venv/bin/activate
python $PATH_TO_BINARY_NINJA_INSTALLATION/scripts/install_api.py
```

### Testing local versions of the plugin

To test the plugin locally in your own Binary Ninja installation during development, create a symbolic link between your development folder, and the [Binary Ninja user plugins folder](https://docs.binary.ninja/guide/index.html#user-folder), so that your development folder is loaded by Binary Ninja on startup as a plugin.

- MacOS:

    ```sh
    ln -s `pwd` ~/Library/Application\ Support/Binary\ Ninja/plugins/rust_metadata_carver
    ```

- Linux:

    ```sh
    ln -s --relative . ~/.binaryninja/plugins/rust_metadata_carver
    ```

- Windows (Powershell):
    ```powershell
    New-Item -ItemType Junction -Value $(Get-Location) -Path "$env:APPDATA\Binary Ninja\plugins\rust_metadata_carver"
    ```

You should then change the values of the following Python settings in Binary Ninja to point to inside your development folder's virtual environment:

- `python.binaryOverride`: Set this to the path of the Python interpreter inside your development virtual environment, e.g. `$DEVELOPMENT_FOLDER/rust_string_slicer/.venv/bin/python/`
- `python.virtualenv`: Set this to the path of the `site-packages` directory inside your development virtual environment, e.g. `$DEVELOPMENT_FOLDER/rust_string_slicer/.venv/lib/python3.11/site-packages
