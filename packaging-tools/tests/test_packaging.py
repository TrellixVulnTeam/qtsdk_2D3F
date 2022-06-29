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

import os
import platform
import sys
import unittest
from fileinput import FileInput
from shutil import rmtree
from tempfile import mkdtemp

from create_installer import parsePackageFinalizeItems
from patch_qt import (
    patchAbsoluteLibPathsFromLine,
    patchQConfigPriFromLine,
    patchQmakePrlBuildDirFromLine,
    patchQtEdition,
)
from runner import do_execute_sub_process


class TestPackaging(unittest.TestCase):

    def test_patchAbsoluteLibPaths(self):
        testData = (("QMAKE_LIBS_ZLIB = /opt/android/android-ndk-r18b/platforms/android-21/arch-arm64/usr/lib/libz.so",
                     "QMAKE_LIBS_ZLIB = -lz",
                     "pri"),
                    ("QMAKE_LIBS_GTK3 = /lib64/libgtk-3.so /lib64/libgdk-3.so /lib64/libatk-1.0.so /lib64/libgio-2.0.so /lib64/libpangocairo-1.0.so /lib64/libgdk_pixbuf-2.0.so /lib64/libcairo-gobject.so /lib64/libpango-1.0.so /lib64/libcairo.so /lib64/libgobject-2.0.so /lib64/libglib-2.0.so",
                     "QMAKE_LIBS_GTK3 = -lgtk-3 -lgdk-3 -latk-1.0 -lgio-2.0 -lpangocairo-1.0 -lgdk_pixbuf-2.0 -lcairo-gobject -lpango-1.0 -lcairo -lgobject-2.0 -lglib-2.0",
                     "pri"),
                    ("QMAKE_PRL_LIBS = /opt/android/android-ndk-r18b/sources/cxx-stl/llvm-libc++/libs/arm64-v8a/libc++.so.21 /lib64/libglib-2.0.so",
                     "QMAKE_PRL_LIBS = -lc++ -lglib-2.0",
                     "prl"),
                    ("QMAKE_PRL_LIBS = -LC:\\opensslx86\\lib -L$$[QT_INSTALL_LIBS] $$[QT_INSTALL_LIBS]\\Qt5Cored.lib",
                     "QMAKE_PRL_LIBS = -LC:\\opensslx86\\lib -L$$[QT_INSTALL_LIBS] $$[QT_INSTALL_LIBS]\\Qt5Cored.lib",
                     "prl"),
                    ("QMAKE_PRL_LIBS = -LC:\\openssl\\lib \"C:\\Program Files (x86)\\Windows Kits\\10\\lib\\10.0.16299.0\\um\\x64\\shell32.lib\" $$[QT_INSTALL_LIBS]\\Qt5Cored.lib",
                     "QMAKE_PRL_LIBS = -LC:\\openssl\\lib \"-lshell32\" $$[QT_INSTALL_LIBS]\\Qt5Cored.lib",
                     "prl"),
                    ("QMAKE_PRL_LIBS = -LC:\\openssl\\lib \"C:\\Program Files (x86)\\Windows Kits\\10\\lib\\10.0.16299.0\\um\\x64\\shell32.foobar\" $$[QT_INSTALL_LIBS]\\Qt5Cored.foobar",
                     "QMAKE_PRL_LIBS = -LC:\\openssl\\lib \"C:\\Program Files (x86)\\Windows Kits\\10\\lib\\10.0.16299.0\\um\\x64\\shell32.foobar\" $$[QT_INSTALL_LIBS]\\Qt5Cored.foobar",
                     "prl"),
                    ("QMAKE_PRL_LIBS = $$[QT_INSTALL_LIBS]/qtfreetyped.lib \"C:\\Program Files (x86)\\Windows Kits\\10\\lib\\10.0.16299.0\\um\\x86/dwrite.lib\"",
                     "QMAKE_PRL_LIBS = $$[QT_INSTALL_LIBS]/qtfreetyped.lib \"-ldwrite\"",
                     "prl"),
                    ("_qt5gui_find_extra_libs(EGL \"c:/Utils/Android/android-ndk-r18b/platforms/android-16/arch-x86/usr/lib/libEGL.so\" \"\" \"\")",
                     "_qt5gui_find_extra_libs(EGL \"EGL\" \"\" \"\")",
                     "cmake"),
                    ("_qt5gui_find_extra_libs(EGL \"/opt/android/android-ndk-r18b/platforms/android-21/arch-arm64/usr/lib/libEGL.so\" \"\" \"\")",
                     "_qt5gui_find_extra_libs(EGL \"EGL\" \"\" \"\")",
                     "cmake"),
                    ("get_filename_component(_qt5Gui_install_prefix \"${CMAKE_CURRENT_LIST_DIR}/../../../\" ABSOLUTE)",
                     "get_filename_component(_qt5Gui_install_prefix \"${CMAKE_CURRENT_LIST_DIR}/../../../\" ABSOLUTE)",
                     "cmake"),
                    ("/Applications/Xcode10.app/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS12.1.sdk/usr/lib/libz.tbd -framework OpenGLES",
                     "-lz -framework OpenGLES",
                     "prl"))

        for data in testData:
            result = patchAbsoluteLibPathsFromLine(data[0], data[2])
            self.assertEqual(result, data[1], f"Failed to patch: [{data[0]}] as: [{data[1]}]")

    def test_patchQmakePrlBuildDirFromLine(self):
        testData = (("QMAKE_PRL_BUILD_DIR = /foo/bar", ""),
                    ("QMAKE_PRL_BUILD_DIR=   /foo/bar", ""),
                    ("foo bar = /foo/bar", "foo bar = /foo/bar"))

        for data in testData:
            result = patchQmakePrlBuildDirFromLine(data[0])
            self.assertEqual(result, data[1], f"Failed to patch: [{data[0]}] as: [{data[1]}]")

    def test_patchQConfigPriFromLine(self):
        testData = (("QMAKE_DEFAULT_LIBDIRS = /foo/bar", "QMAKE_DEFAULT_LIBDIRS ="),
                    ("QMAKE_DEFAULT_INCDIRS =   /foo/bar", "QMAKE_DEFAULT_INCDIRS ="),
                    ("foo bar = /foo/bar", "foo bar = /foo/bar"))

        for data in testData:
            result = patchQConfigPriFromLine(data[0])
            self.assertEqual(result, data[1], f"Failed to patch: [{data[0]}] as: [{data[1]}]. Got: [{result}]")

    def test_parsePackageFinalizeItems(self):
        testData = (("set_executable=licheck64, foo=bar, set_executable=something", "set_executable", ["licheck64", "something"]),
                    ("set_executable=licheck64,foo=bar,   set_executable = something", "set_executable", ["licheck64", "something"]),
                    ("set_executable=licheck64", "set_executable", ["licheck64"]))

        for data in testData:
            matchCount = 0
            for item in parsePackageFinalizeItems(data[0], data[1]):
                self.assertIn(item, data[2])
                matchCount += 1
            self.assertEqual(matchCount, len(data[2]))

    def test_patchQtEdition(self):
        tempDir = mkdtemp(dir=os.getcwd())
        tempFile = os.path.join(tempDir, "qconfig.pri")

        try:
            with open(tempFile, "a") as f:
                f.write("something foo\n")
                f.write("QT_EDITION = foobar\n")
                f.write("nonsense\n")

            licheckName = "licheck_foo"
            releaseTimeStamp = "11223344"
            patchQtEdition(tempDir, licheckName, releaseTimeStamp)

            expectedData = []
            expectedData.append("something foo")
            expectedData.append("QT_EDITION = Enterprise")
            expectedData.append("QT_LICHECK = " + licheckName)
            expectedData.append("QT_RELEASE_DATE = " + releaseTimeStamp)
            expectedData.append("nonsense")

            idx = 0
            for line in FileInput(tempFile, inplace=False):
                print(f"Received data: [{line.strip()}] expected data: [{expectedData[idx]}]")
                self.assertEqual(line.strip(), expectedData[idx], f"Received data: [{line}] differs from expected data: [{expectedData[idx]}]")
                idx += 1
        finally:
            rmtree(tempDir)

    @unittest.skipUnless(os.environ.get("PKG_TEST_QT_CONFIG_BASE_PATH"), "Skipping because 'PKG_TEST_QT_CONFIG_BASE_PATH' is not set")
    @unittest.skipUnless(os.environ.get("PKG_TEST_QT_ARTIFACTS_URL"), "Skipping because 'PKG_TEST_QT_CONFIG_BASE_PATH' is not set")
    @unittest.skipUnless(os.environ.get("PKG_TEST_QT_IFW_TOOL_URL"), "Skipping because 'PKG_TEST_QT_IFW_TOOL_URL' is not set")
    def test_createInstaller(self):
        extension = '.run' if platform.system().lower().startswith('linux') else ''
        testsDir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.environ.get("PKG_TEST_QT_CONFIG_BASE_PATH"), "offline_installer_jobs", "5.9.3")
        offlineJobs = os.listdir(path)
        for offlineJob in offlineJobs:
            cmd_args = [sys.executable, '-u', os.path.join(testsDir, 'create_installer.py')]
            cmd_args = cmd_args + ['-c', os.environ.get("PKG_TEST_QT_CONFIG_BASE_PATH")]
            cmd_args = cmd_args + ['-f', os.path.join(path, offlineJob)]
            cmd_args = cmd_args + ['--offline']
            cmd_args = cmd_args + ['-l', 'enterprise']
            cmd_args = cmd_args + ['-u', os.environ.get("PKG_TEST_QT_ARTIFACTS_URL")]
            cmd_args = cmd_args + ['--ifw-tools=' + os.environ.get("PKG_TEST_QT_IFW_TOOL_URL")]
            cmd_args = cmd_args + ['--preferred-installer-name=' + offlineJob]
            cmd_args = cmd_args + ['--dry-run']
            try:
                do_execute_sub_process(cmd_args, os.getcwd())
            except Exception as e:
                self.fail(f"Failed to execute: [{' '.join(cmd_args)}] -> {str(e)}")
            self.assertTrue(os.path.exists(os.path.join(testsDir, 'installer_output', offlineJob + extension)), "No installers generated")


if __name__ == '__main__':
    unittest.main()
