#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# The embedded C contexts retain their upstream authorship:
# Copyright (C) Jay Sorg 2009-2012
# Copyright (C) Laxmikant Rashinkar 2012
# Source: https://github.com/neutrinolabs/xrdp/blob/v0.10.1/sesman/chansrv/clipboard.c
# License: https://github.com/neutrinolabs/xrdp/blob/v0.10.1/COPYING
#          https://www.apache.org/licenses/LICENSE-2.0
# The upstream file is Apache-2.0, not GPL.
# Related proposal (unmerged when reviewed):
# https://github.com/neutrinolabs/xrdp/pull/3805
# Protocol length requirement, MS-RDPECLIP 3.1.5.1:
# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpeclip/ed611844-4eb3-4b29-ac8c-2576151b4d0c

"""Apply eight strictly matched edits to xrdp 0.10.1 clipboard.c.

Usage: python3 patch-xrdp-clipboard.py /path/to/sesman/chansrv/clipboard.c

The edits advertise only CF_UNICODETEXT for text and remove six trailing
32-bit zeros outside their declared CLIPRDR payload lengths. Image/file
payloads, conversion code, XFlush calls and timeout behavior are unchanged.
All eight contexts must be original, or all eight must already be patched.
Mixed or unexpected source is rejected before writing. No network access,
clipboard access, compilation or service operations are performed.
"""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


class PatchError(Exception):
    """The input does not match the reviewed patch contexts."""


@dataclass(frozen=True)
class Edit:
    label: str
    function: str
    before: str
    after: str


EDITS = (
    Edit(
        "capabilities: remove trailing four bytes",
        "clipboard_init",
        "        out_uint32_le(s, g_cliprdr_flags); /* generalFlags */\n"
        "        out_uint32_le(s, 0); /* extra 4 bytes ? */\n"
        "        s_mark_end(s);",
        "        out_uint32_le(s, g_cliprdr_flags); /* generalFlags */\n"
        "        s_mark_end(s);",
    ),
    Edit(
        "monitor ready: remove trailing four bytes",
        "clipboard_init",
        "        out_uint32_le(s, 0); /* dataLen */\n"
        "        out_uint32_le(s, 0); /* extra 4 bytes ? */\n"
        "        s_mark_end(s);",
        "        out_uint32_le(s, 0); /* dataLen */\n"
        "        s_mark_end(s);",
    ),
    Edit(
        "data request: remove trailing four bytes",
        "clipboard_send_data_request",
        "    out_uint32_le(s, format_id);\n"
        "    out_uint32_le(s, 0);\n"
        "    s_mark_end(s);",
        "    out_uint32_le(s, format_id);\n"
        "    s_mark_end(s);",
    ),
    Edit(
        "format acknowledgment: remove trailing four bytes",
        "clipboard_send_format_ack",
        "    out_uint32_le(s, 0); /* length */\n"
        "    out_uint32_le(s, 0); /* extra 4 bytes */\n"
        "    s_mark_end(s);",
        "    out_uint32_le(s, 0); /* length */\n"
        "    s_mark_end(s);",
    ),
    Edit(
        "long format names: advertise CF_UNICODETEXT only",
        "clipboard_send_format_announce",
        "            case XRDP_CB_TEXT:\n"
        "                LOG_DEVEL(LOG_LEVEL_DEBUG, \"clipboard_send_format_announce: XRDP_CB_TEXT\");\n"
        "                /* canned response for \"bitmap\" */\n"
        "                out_uint32_le(s, 0x0000000d);\n"
        "                clipboard_out_utf8_as_utf16_le(s, \"\");\n"
        "                out_uint32_le(s, 0x00000010);\n"
        "                clipboard_out_utf8_as_utf16_le(s, \"\");\n"
        "                out_uint32_le(s, 0x00000001);\n"
        "                clipboard_out_utf8_as_utf16_le(s, \"\");\n"
        "                out_uint32_le(s, 0x00000007);\n"
        "                clipboard_out_utf8_as_utf16_le(s, \"\");\n"
        "                break;",
        "            case XRDP_CB_TEXT:\n"
        "                LOG_DEVEL(LOG_LEVEL_DEBUG, \"clipboard_send_format_announce: XRDP_CB_TEXT\");\n"
        "                /* Advertise only the text format this server can provide. */\n"
        "                out_uint32_le(s, CF_UNICODETEXT);\n"
        "                clipboard_out_utf8_as_utf16_le(s, \"\");\n"
        "                break;",
    ),
    Edit(
        "short format names: advertise CF_UNICODETEXT only",
        "clipboard_send_format_announce",
        "            case XRDP_CB_TEXT:\n"
        "                LOG_DEVEL(LOG_LEVEL_DEBUG, \"clipboard_send_format_announce: XRDP_CB_TEXT\");\n"
        "                /* canned response for \"bitmap\" */\n"
        "                out_uint32_le(s, 0x0000000d);\n"
        "                out_uint8p(s, windows_native_format, sizeof(windows_native_format));\n"
        "                out_uint32_le(s, 0x00000010);\n"
        "                out_uint8p(s, windows_native_format, sizeof(windows_native_format));\n"
        "                out_uint32_le(s, 0x00000001);\n"
        "                out_uint8p(s, windows_native_format, sizeof(windows_native_format));\n"
        "                out_uint32_le(s, 0x00000007);\n"
        "                out_uint8p(s, windows_native_format, sizeof(windows_native_format));\n"
        "                break;",
        "            case XRDP_CB_TEXT:\n"
        "                LOG_DEVEL(LOG_LEVEL_DEBUG, \"clipboard_send_format_announce: XRDP_CB_TEXT\");\n"
        "                /* Advertise only the text format this server can provide. */\n"
        "                out_uint32_le(s, CF_UNICODETEXT);\n"
        "                out_uint8p(s, windows_native_format, sizeof(windows_native_format));\n"
        "                break;",
    ),
    Edit(
        "format list: remove trailing four bytes after declared payload",
        "clipboard_send_format_announce",
        "    holdp[3] = (size >> 24) & 0xff;\n"
        "    out_uint32_le(s, 0);\n"
        "    s_mark_end(s);",
        "    holdp[3] = (size >> 24) & 0xff;\n"
        "    s_mark_end(s);",
    ),
    Edit(
        "text response: retain UTF-16 NUL, remove trailing four bytes",
        "clipboard_send_data_response_for_text",
        "    out_uint16_le(s, 0); /* nil for string */\n"
        "    out_uint32_le(s, 0);\n"
        "    s_mark_end(s);",
        "    out_uint16_le(s, 0); /* nil for string */\n"
        "    s_mark_end(s);",
    ),
)


def function_region(source, name):
    """Locate one definition in the upstream's column-zero brace style."""
    pattern = re.compile(r"^" + re.escape(name) + r"\([^;{}]*\)\n\{", re.M)
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise PatchError(f"{name}: expected one definition, found {len(matches)}")
    start = matches[0].start()
    end = source.find("\n}\n", matches[0].end())
    if end < 0:
        raise PatchError(f"{name}: cannot locate closing brace")
    return start, end + 3


def patch_source(source):
    if "\r" in source:
        raise PatchError("expected LF line endings; refusing implicit normalization")
    states = []
    for edit in EDITS:
        start, end = function_region(source, edit.function)
        body = source[start:end]
        old_count, new_count = body.count(edit.before), body.count(edit.after)
        if (old_count, new_count) == (1, 0):
            states.append("original")
        elif (old_count, new_count) == (0, 1):
            states.append("patched")
        else:
            raise PatchError(
                f"{edit.label}: expected one original or patched context; "
                f"found original={old_count}, patched={new_count}"
            )
    if all(state == "patched" for state in states):
        return source, False
    if any(state == "patched" for state in states):
        raise PatchError("partially patched source; restore the original before applying")

    result = source
    for edit in EDITS:
        start, end = function_region(result, edit.function)
        body = result[start:end]
        if body.count(edit.before) != 1:
            raise PatchError(f"{edit.label}: context changed during patch preparation")
        result = result[:start] + body.replace(edit.before, edit.after, 1) + result[end:]
    return result, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clipboard_c", type=Path, help="xrdp source clipboard.c to patch")
    args = parser.parse_args()
    path = args.clipboard_c
    temporary = None
    try:
        if path.is_symlink() or not path.is_file():
            raise PatchError("input must be a regular file, not a symlink")
        original = path.read_bytes()
        result, changed = patch_source(original.decode("utf-8"))
        if not changed:
            print(f"Already patched: {path} (no changes)")
            return 0
        mode = stat.S_IMODE(path.stat().st_mode)
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".clipboard-patch-", dir=path.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(result.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        if path.read_bytes() != original:
            raise PatchError("input changed while preparing the patch; refusing replacement")
        os.replace(temporary, path)
        temporary = None
    except (OSError, UnicodeError, PatchError) as error:
        print(f"ERROR: {error}; no patch written", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    print(f"Patched: {path} (2 text format lists; 6 trailing 32-bit zeros)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
