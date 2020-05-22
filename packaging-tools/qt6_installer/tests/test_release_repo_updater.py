#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#############################################################################
##
## Copyright (C) 2020 The Qt Company Ltd.
## Contact: https://www.qt.io/licensing/
##
## This file is part of the release tools of the Qt Toolkit.
##
## $QT_BEGIN_LICENSE:GPL-EXCEPT$
## Commercial License Usage
## Licensees holding valid commercial Qt licenses may use this file in
## accordance with the commercial license agreement provided with the
## Software or, alternatively, in accordance with the terms contained in
## a written agreement between you and The Qt Company. For licensing terms
## and conditions see https://www.qt.io/terms-conditions. For further
## information use the contact form at https://www.qt.io/contact-us.
##
## GNU General Public License Usage
## Alternatively, this file may be used under the terms of the GNU
## General Public License version 3 as published by the Free Software
## Foundation with exceptions as appearing in the file LICENSE.GPL3-EXCEPT
## included in the packaging of this file. Please review the following
## information to ensure the GNU General Public License requirements will
## be met: https://www.gnu.org/licenses/gpl-3.0.html.
##
## $QT_END_LICENSE$
##
#############################################################################

import testhelpers
import os
import unittest
from ddt import ddt
import tempfile
import shutil
import configparser
from installer_utils import PackagingError
from release_task_reader import parse_data
from release_repo_updater import is_safe_repo_directory, upload_ifw_to_remote, upload_pending_repository_content, \
                                    remote_repository_exists, reset_new_remote_repository, create_remote_repository_backup, \
                                    update_remote_repository, remote_file_exists, build_online_repositories, \
                                    ensure_ext_repo_paths, parse_ext, check_repogen_output, append_to_task_filters


def _write_dummy_file(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w+') as f:
        f.write("\n")


def _write_package_xml(path: str, version: str, releaseDate: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w+') as f:
        f.write("<?xml version=\"1.0\"?>\n")
        f.write("<Package>\n")
        f.write("  <Name>qt.foo.bar1</Name>\n")
        f.write("  <DisplayName>Test</DisplayName>\n")
        f.write("  <Description>Test</Description>\n")
        f.write("  <Version>{0}</Version>\n".format(version))
        f.write("  <ReleaseDate>{0}</ReleaseDate>\n".format(releaseDate))
        f.write("</Package>\n")


def _write_updates_xml(path: str, version: str, releaseDate: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w+') as f:
        f.write("<Updates>\n")
        f.write("  <ApplicationName>{AnyApplication}</ApplicationName>\n")
        f.write("  <ApplicationVersion>1.0.0</ApplicationVersion>\n")
        f.write("  <Checksum>false</Checksum>\n")
        f.write("  <PackageUpdate>\n")
        f.write("    <Name>qt.foo.bar1</Name>\n")
        f.write("    <DisplayName>Foo bar</DisplayName>\n")
        f.write("    <Description>Foo and bar</Description>\n")
        f.write("    <Version>{0}</Version>\n".format(version))
        f.write("    <ReleaseDate>{0}</ReleaseDate>\n".format(releaseDate))
        f.write("    <DownloadableArchives/>\n")
        f.write("    <UpdateFile CompressedSize=\"0\" OS=\"Any\" UncompressedSize=\"0\"/>\n")
        f.write("    <SHA1>c1559cbb0f0983909f7229dc79dfdf7eab46cd52</SHA1>\n")
        f.write("  </PackageUpdate>\n")
        f.write("</Updates>\n")


async def _get_repogen() -> str:
    ifwTools = "http://ci-files02-hki.intra.qt.io/packages/jenkins/enterprise/ifw/unifiedqt/installer-framework-build-tqtc_3.2-linux-x64.7z"
    server = "127.0.0.1"
    serverHome = os.path.expanduser("~")
    return await upload_ifw_to_remote(ifwTools, server, serverHome)


@ddt
class TestReleaseRepoUpdater(unittest.TestCase):
    server = None  # type: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = "127.0.0.1"

    @testhelpers.asyncio_test_parallel_data(os.path.expanduser("~"), "/", "/home", "/bin", "/boot", "/sys", "/sbin", "/lib",
                                            "/home/foo/..", os.path.join(os.path.expanduser("~"), "foo", "bar", "..", ".."),
                                            unpack=False)
    async def test_is_not_safe_repo_directory(self, path: str) -> None:
        with self.assertRaises(PackagingError, msg="Should have raised Exception for path: {0}".format(path)):
            is_safe_repo_directory([path])

    @testhelpers.asyncio_test_parallel_data(os.path.join(os.path.expanduser("~"), "foo"), "/etc/foo", "/data/bar", unpack=False)
    async def test_is_safe_repo_directory(self, path: str) -> None:
        is_safe_repo_directory([path])

    @testhelpers.asyncio_test
    async def test_remote_repository_exists(self):
        self.assertTrue(remote_repository_exists(self.server, os.getcwd()))
        self.assertFalse(remote_repository_exists(self.server, "/some/bogus/directory/foo"))

    @testhelpers.asyncio_test
    async def test_remote_file_exists(self):
        self.assertTrue(remote_file_exists(self.server, os.path.abspath(__file__)))
        self.assertFalse(remote_file_exists(self.server, "/some/bogus/directory/foo.txt"))

    @unittest.skipUnless(testhelpers.isInternalFileServerReachable(), "Skipping because file server is not accessible")
    @testhelpers.asyncio_test
    async def test_upload_ifw_to_remote(self) -> None:
        try:
            repogen = await _get_repogen()
        finally:
            self.assertTrue(os.path.isfile(repogen))
            shutil.rmtree(os.path.dirname(repogen))

    @testhelpers.asyncio_test
    async def test_upload_pending_repository_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.getcwd(), prefix="_repo_tmp_") as tmpBaseDir:
            sourceRepo = os.path.join(tmpBaseDir, "repository")
            destinationRepo = os.path.join(tmpBaseDir, "destination_online_repository")

            _write_dummy_file(os.path.join(sourceRepo, "qt.foo.bar1", "meta", "package.xml"))
            _write_dummy_file(os.path.join(sourceRepo, "qt.foo.bar2", "meta", "package.xml"))
            _write_dummy_file(os.path.join(sourceRepo, "Updates.xml"))

            upload_pending_repository_content(self.server, sourceRepo, destinationRepo)
            self.assertListEqual(sorted(os.listdir(sourceRepo)), sorted(os.listdir(destinationRepo)))

    @testhelpers.asyncio_test
    async def test_reset_new_remote_repository(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.getcwd(), prefix="_repo_tmp_") as tmpBaseDir:
            remoteSourceRepoPath = os.path.join(tmpBaseDir, "repository")
            remoteTargetRepoPath = os.path.join(tmpBaseDir, "destination_online_repository")

            _write_dummy_file(os.path.join(remoteSourceRepoPath, "qt.foo.bar1", "meta", "package.xml"))
            _write_dummy_file(os.path.join(remoteSourceRepoPath, "qt.foo.bar2", "meta", "package.xml"))
            _write_dummy_file(os.path.join(remoteSourceRepoPath, "Updates.xml"))

            reset_new_remote_repository(self.server, remoteSourceRepoPath, remoteTargetRepoPath)
            self.assertTrue(os.path.isfile(os.path.join(remoteTargetRepoPath, "qt.foo.bar1", "meta", "package.xml")))
            self.assertTrue(os.path.isfile(os.path.join(remoteTargetRepoPath, "qt.foo.bar2", "meta", "package.xml")))
            self.assertTrue(os.path.isfile(os.path.join(remoteTargetRepoPath, "Updates.xml")))

            # should not allow resetting a new repository if destination already contains a repository (should 'update' instead)
            with self.assertRaises(PackagingError):
                reset_new_remote_repository(self.server, remoteSourceRepoPath, remoteTargetRepoPath)

    @testhelpers.asyncio_test
    async def test_create_remote_repository_backup(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.getcwd(), prefix="_repo_tmp_") as tmpBaseDir:
            remoteSourceRepoPath = os.path.join(tmpBaseDir, "repository")

            _write_dummy_file(os.path.join(remoteSourceRepoPath, "qt.foo.bar1", "meta", "package.xml"))
            _write_dummy_file(os.path.join(remoteSourceRepoPath, "qt.foo.bar2", "meta", "package.xml"))
            _write_dummy_file(os.path.join(remoteSourceRepoPath, "Updates.xml"))

            remoteRepoBackupPath = create_remote_repository_backup(self.server, remoteSourceRepoPath)
            self.assertListEqual(sorted(os.listdir(remoteSourceRepoPath)), sorted(os.listdir(remoteRepoBackupPath)))

    @unittest.skipUnless(testhelpers.isInternalFileServerReachable(), "Skipping because files erver is not accessible")
    @testhelpers.asyncio_test
    async def test_update_remote_repository(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.getcwd(), prefix="_repo_tmp_") as tmpBaseDir:
            remoteTargetRepoPath = os.path.join(tmpBaseDir, "destination_online_repository")

            # initial repository
            remoteSourceRepoPath = os.path.join(tmpBaseDir, "repository")
            componentPath = os.path.join(remoteSourceRepoPath, "qt.foo.bar1")
            componentMetaPath = os.path.join(componentPath, "meta")
            os.makedirs(componentMetaPath, exist_ok=True)
            packageXml = os.path.join(componentMetaPath, "package.xml")
            _write_package_xml(packageXml, "1.0", "2020-01-01")
            updatesXml = os.path.join(remoteSourceRepoPath, "Updates.xml")
            _write_updates_xml(updatesXml, "1.0", "2020-01-01")

            # initialize new repository
            reset_new_remote_repository(self.server, remoteSourceRepoPath, remoteTargetRepoPath)

            expectedPackageXmlPath = os.path.join(remoteTargetRepoPath, "qt.foo.bar1", "meta", "package.xml")
            expectedUpdatesXmlPath = os.path.join(remoteTargetRepoPath, "Updates.xml")
            self.assertTrue(os.path.isfile(expectedPackageXmlPath), "Did not find package.xml from expected path: {0}".format(expectedPackageXmlPath))
            self.assertTrue(os.path.isfile(expectedUpdatesXmlPath), "Did not find Updates.xml from expected path: {0}".format(expectedUpdatesXmlPath))

            # create new repository snapshot
            remoteSourceRepoPath_pkg = os.path.join(tmpBaseDir, "pkg")
            componentPath_pkg = os.path.join(remoteSourceRepoPath_pkg, "qt.foo.bar1")
            componentMetaPath_pkg = os.path.join(componentPath_pkg, "meta")
            os.makedirs(componentMetaPath_pkg, exist_ok=True)
            packageXml = os.path.join(componentMetaPath_pkg, "package.xml")
            _write_package_xml(packageXml, "1.1", "2020-01-02")

            try:
                # we need repogen tools
                remoteRepogen = await _get_repogen()
                # now update
                update_remote_repository(self.server, remoteRepogen, remoteSourceRepoPath_pkg, remoteTargetRepoPath)
            finally:
                self.assertTrue(os.path.isfile(remoteRepogen))
                shutil.rmtree(os.path.dirname(remoteRepogen))

            # verify the update actually was done
            with open(os.path.join(remoteTargetRepoPath, "Updates.xml"), 'r') as f:
                for line in f:
                    trimmedLine = line.strip()
                    if trimmedLine.startswith("<Version>"):
                        self.assertEqual(trimmedLine, "<Version>1.1</Version>")
                        break

    @testhelpers.asyncio_test_parallel_data((True, True), (False, False), ("yes", True), ("1", True), ("y", True),
                                            ("false", False), ("n", False), ("0", False), ("no", False))
    async def test_string_to_bool(self, value: str, expectedResult: bool) -> None:
        from release_repo_updater import string_to_bool
        self.assertEqual(string_to_bool(value), expectedResult)

    @testhelpers.asyncio_test
    async def test_build_online_repositories_dryrun(self) -> None:
        sample_config = """
            [task.repository.linux.x86_64.repo1]
            config_file: foobar_config_file
            repo_path: foo/bar/path_1
        """
        config = configparser.ConfigParser()
        config.read_string(sample_config)

        # parse all tasks i.e. no filters
        tasks = parse_data(config, task_filters=[])
        await build_online_repositories(tasks=tasks, license="opensource", installerConfigBaseDir="foo", artifactShareBaseUrl="foo",
                                        ifwTools="foo", buildRepositories=False)
        task = tasks.pop()
        self.assertTrue(task.source_online_repository_path.endswith("foo/bar/path_1/online_repository"))
        self.assertTrue(task.source_pkg_path.endswith("foo/bar/path_1/pkg"))

    @testhelpers.asyncio_test
    async def test_ensure_ext_repo_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.getcwd(), prefix="_repo_tmp_") as tmpBaseDir:
            expectedRepo = os.path.join(tmpBaseDir, "some", "test", "path")
            await ensure_ext_repo_paths(self.server, self.server, expectedRepo)
            self.assertTrue(os.path.isdir(expectedRepo))

    @testhelpers.asyncio_test_parallel_data(
        ("user@server.com:/foo/bar"),
        ("server.com:/foo/bar"),
        ("user@server.com:/"), unpack=False
    )
    async def test_parse_ext_valid(self, ext) -> None:
        parse_ext(ext)

    @testhelpers.asyncio_test_parallel_data(
        ("user@server.com"),
        ("server.com:/foo/bar:"),
        ("user@server.com:some/path"), unpack=False
    )
    async def test_parse_ext_invalid(self, ext) -> None:
        with self.assertRaises(PackagingError):
            parse_ext(ext)

    @testhelpers.asyncio_test_parallel_data(
        ("Error: Repository parameter missing argument"),
        ("Invalid content in ..."),
        ("Repository target directory /foobar/bar/foo already exists."), unpack=False
    )
    async def test_check_invalid_repogen_output(self, repogen_output) -> None:
        with self.assertRaises(PackagingError):
            check_repogen_output(repogen_output)

    @testhelpers.asyncio_test_parallel_data(
        ("Update component a.b.c.d"),
        ("Cannot find new components to update"), unpack=False
    )
    async def test_check_valid_repogen_output(self, repogen_output) -> None:
            # should not throw exception
            check_repogen_output(repogen_output)

    @testhelpers.asyncio_test_parallel_data(([], ["repository"]),
                                            (["linux,common"], ["repository,linux,common"]),
                                            (["", "linux,common"], ["repository", "repository,linux,common"])
    )
    async def test_append_to_task_filters(self, task_filters: str, expected_result: bool) -> None:
        self.assertEqual(append_to_task_filters(task_filters, "repository"), expected_result)


if __name__ == '__main__':
    unittest.main()
