#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#############################################################################
#
# Copyright (C) 2022 The Qt Company Ltd.
# Contact: https://www.qt.io/licensing/
#
# This file is part of the release tools of the Qt Toolkit.
#
# $QT_BEGIN_LICENSE:GPL-EXCEPT$
# Commercial License Usage
# Licensees holding valid commercial Qt licenses may use this file in
# accordance with the commercial license agreement provided with the
# Software or, alternatively, in accordance with the terms contained in
# a written agreement between you and The Qt Company. For licensing terms
# and conditions see https://www.qt.io/terms-conditions. For further
# information use the contact form at https://www.qt.io/contact-us.
#
# GNU General Public License Usage
# Alternatively, this file may be used under the terms of the GNU
# General Public License version 3 as published by the Free Software
# Foundation with exceptions as appearing in the file LICENSE.GPL3-EXCEPT
# included in the packaging of this file. Please review the following
# information to ensure the GNU General Public License requirements will
# be met: https://www.gnu.org/licenses/gpl-3.0.html.
#
# $QT_END_LICENSE$
#
#############################################################################

from ddt import ddt, data
import os
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from create_installer import remove_all_debug_libraries
from bldinstallercommon import locate_paths
from bld_utils import is_windows, is_macos


@ddt
class TestCommon(unittest.TestCase):
    @data((
        ("/bin/", "/lib/", "/qml/", "/plugins/", "/unrelated/"),
        (
            "Foo.lib", "Food.lib", "dd.qml", "Bar.exe", "Bard.exe", "Qt3d.dll", "Qt3dd.dll"
        ) if is_windows() else (
            "foo_debug.bar", ".foo_debug.bar", "_debug.bar", "foo_debug.", "foodebugbar"
        ),
        [
            'Foo.lib', 'dd.qml', 'Bard.exe', 'Qt3d.dll', 'Bar.exe'
        ] if is_windows() else ["foodebugbar"]
    ))
    @unittest.skipIf(not(is_windows() or is_macos()), "This test is only for Windows and macOS")
    def test_remove_all_debug_libraries_win(self, data):
        dirs, files, remaining_files = data
        with TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            for dir in dirs:
                Path(tmpdir + dir).mkdir()
                for file in files:
                    Path(tmpdir + dir + file).touch()
            remove_all_debug_libraries(tmpdir)
            for dir in dirs:
                result_paths = locate_paths(tmpdir + dir, ["*"], [os.path.isfile])
                result_rel = [str(Path(p).relative_to(tmpdir + dir)) for p in result_paths]
                if dir == "/unrelated/":
                    self.assertCountEqual(result_rel, files)
                else:
                    self.assertCountEqual(result_rel, remaining_files)


if __name__ == "__main__":
    unittest.main()
