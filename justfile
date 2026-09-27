# Build, check and upload to PyPI. The token comes from the macOS Keychain entry pypi-pytailor.
publish:
    rm -rf dist
    uv build
    uvx twine check dist/*
    UV_PUBLISH_TOKEN="$(security find-generic-password -s pypi-pytailor -w)" uv publish
