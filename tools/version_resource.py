"""The version resource built into HyperFetch.exe.

What the file is, who made it and which version: the Details page of its
Properties, and what Windows and antivirus see when they ask an exe about
itself. The exe used to carry none. HyperFetch.spec writes `version_file()`
out and hands it to PyInstaller; tests/test_build_trust.py says why.
"""
import re

COMPANY = "HyperFetch"                  # installer.iss: AppPublisher
PRODUCT = "HyperFetch"
COPYRIGHT = "Copyright (c) 2026 Tanumay Goswami. MIT License."
EXE = "HyperFetch.exe"

_TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%(numbers)s,
    prodvers=%(numbers)s,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [StringStruct('CompanyName', '%(company)s'),
           StringStruct('FileDescription', '%(product)s'),
           StringStruct('FileVersion', '%(version)s'),
           StringStruct('InternalName', '%(product)s'),
           StringStruct('LegalCopyright', '%(copyright)s'),
           StringStruct('OriginalFilename', '%(exe)s'),
           StringStruct('ProductName', '%(product)s'),
           StringStruct('ProductVersion', '%(version)s')])
      ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def _numbers(version):
    """"2.7.1" -> (2, 7, 1, 0). Windows wants four numbers; a tag after a
    dash ("2.8.1-rc1") is not one of them."""
    parts = [int(n) for n in re.findall(r"\d+", re.split(r"[-+]", version or "")[0])[:3]]
    return tuple(parts + [0] * (4 - len(parts)))


def version_file(version):
    """The text of a PyInstaller version file for this version of the app."""
    return _TEMPLATE % {
        "numbers": _numbers(version), "version": version, "company": COMPANY,
        "product": PRODUCT, "copyright": COPYRIGHT, "exe": EXE}
