# Copyright 2012-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `maasserver.compose_preseed`."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from maasserver.compose_preseed import compose_preseed
from maasserver.enum import (
    NODE_BOOT,
    NODE_STATUS,
    PRESEED_TYPE,
    )
from maasserver.rpc.testing.fixtures import RunningClusterRPCFixture
from maasserver.testing.factory import factory
from maasserver.testing.osystems import make_usable_osystem
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils import absolute_reverse
from maastesting.matchers import MockCalledOnceWith
from metadataserver.models import NodeKey
from provisioningserver.drivers.osystem import BOOT_IMAGE_PURPOSE
from provisioningserver.rpc.exceptions import (
    NoConnectionsAvailable,
    NoSuchOperatingSystem,
    )
from provisioningserver.testing.os import make_osystem
from testtools.matchers import (
    KeysEqual,
    StartsWith,
    )
import yaml


class TestComposePreseed(MAASServerTestCase):

    def test_compose_preseed_for_commissioning_node_produces_yaml(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        preseed = yaml.safe_load(
            compose_preseed(PRESEED_TYPE.COMMISSIONING, node))
        self.assertIn('datasource', preseed)
        self.assertIn('MAAS', preseed['datasource'])
        self.assertThat(
            preseed['datasource']['MAAS'],
            KeysEqual(
                'metadata_url', 'consumer_key', 'token_key', 'token_secret'))

    def test_compose_preseed_for_commissioning_node_has_header(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        preseed = compose_preseed(PRESEED_TYPE.COMMISSIONING, node)
        self.assertThat(preseed, StartsWith("#cloud-config\n"))

    def test_compose_preseed_includes_metadata_url(self):
        node = factory.make_Node(status=NODE_STATUS.READY)
        node.nodegroup.accept()
        self.useFixture(RunningClusterRPCFixture())
        preseed = compose_preseed(PRESEED_TYPE.DEFAULT, node)
        self.assertIn(absolute_reverse('metadata'), preseed)

    def test_compose_preseed_for_commissioning_includes_metadata_url(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        preseed = yaml.safe_load(
            compose_preseed(PRESEED_TYPE.COMMISSIONING, node))
        self.assertEqual(
            absolute_reverse('metadata'),
            preseed['datasource']['MAAS']['metadata_url'])

    def test_compose_preseed_includes_node_oauth_token(self):
        node = factory.make_Node(status=NODE_STATUS.READY)
        node.nodegroup.accept()
        self.useFixture(RunningClusterRPCFixture())
        preseed = compose_preseed(PRESEED_TYPE.DEFAULT, node)
        token = NodeKey.objects.get_token_for_node(node)
        self.assertIn('oauth_consumer_key=%s' % token.consumer.key, preseed)
        self.assertIn('oauth_token_key=%s' % token.key, preseed)
        self.assertIn('oauth_token_secret=%s' % token.secret, preseed)

    def test_compose_preseed_for_commissioning_includes_auth_token(self):
        node = factory.make_Node(status=NODE_STATUS.COMMISSIONING)
        preseed = yaml.safe_load(
            compose_preseed(PRESEED_TYPE.COMMISSIONING, node))
        maas_dict = preseed['datasource']['MAAS']
        token = NodeKey.objects.get_token_for_node(node)
        self.assertEqual(token.consumer.key, maas_dict['consumer_key'])
        self.assertEqual(token.key, maas_dict['token_key'])
        self.assertEqual(token.secret, maas_dict['token_secret'])

    def test_compose_preseed_valid_local_cloud_config(self):
        node = factory.make_Node(status=NODE_STATUS.READY)
        node.nodegroup.accept()
        self.useFixture(RunningClusterRPCFixture())
        preseed = compose_preseed(PRESEED_TYPE.DEFAULT, node)

        keyname = "cloud-init/local-cloud-config"
        self.assertIn(keyname, preseed)

        # Expected input is 'cloud-init/local-cloud-config string VALUE'
        # where one or more spaces in between tokens, and VALUE ending
        # at newline.
        config = preseed[preseed.find(keyname) + len(keyname):]
        value = config.lstrip().split("string")[1].lstrip()

        # Now debconf-unescape it.
        value = value.replace("\\n", "\n").replace("\\\\", "\\")

        # At this point it should be valid yaml.
        data = yaml.safe_load(value)

        self.assertIn("manage_etc_hosts", data)
        self.assertEqual(data["manage_etc_hosts"], False)
        self.assertIn("apt_preserve_sources_list", data)
        self.assertEqual(data["apt_preserve_sources_list"], True)

    def test_compose_preseed_with_curtin_installer(self):
        node = factory.make_Node(
            status=NODE_STATUS.READY, boot_type=NODE_BOOT.FASTPATH)
        node.nodegroup.accept()
        self.useFixture(RunningClusterRPCFixture())
        preseed = yaml.safe_load(
            compose_preseed(PRESEED_TYPE.CURTIN, node))

        self.assertIn('datasource', preseed)
        self.assertIn('MAAS', preseed['datasource'])
        self.assertThat(
            preseed['datasource']['MAAS'],
            KeysEqual(
                'metadata_url', 'consumer_key', 'token_key', 'token_secret'))
        self.assertEqual(
            absolute_reverse('curtin-metadata'),
            preseed['datasource']['MAAS']['metadata_url'])

    def test_compose_preseed_with_osystem_compose_preseed(self):
        os_name = factory.make_name('os')
        osystem = make_osystem(self, os_name, [BOOT_IMAGE_PURPOSE.XINSTALL])
        make_usable_osystem(self, os_name)
        compose_preseed_orig = osystem.compose_preseed
        compose_preseed_mock = self.patch(osystem, 'compose_preseed')
        compose_preseed_mock.side_effect = compose_preseed_orig

        node = factory.make_Node(
            osystem=os_name, status=NODE_STATUS.READY)
        node.nodegroup.accept()
        self.useFixture(RunningClusterRPCFixture())
        token = NodeKey.objects.get_token_for_node(node)
        url = absolute_reverse('curtin-metadata')
        compose_preseed(PRESEED_TYPE.CURTIN, node)
        self.assertThat(
            compose_preseed_mock,
            MockCalledOnceWith(
                PRESEED_TYPE.CURTIN,
                (node.system_id, node.hostname),
                (token.consumer.key, token.key, token.secret),
                url))

    def test_compose_preseed_propagates_NoSuchOperatingSystem(self):
        # If the cluster controller replies that the node's OS is not known to
        # it, compose_preseed() simply passes the exception up.
        os_name = factory.make_name('os')
        osystem = make_osystem(self, os_name, [BOOT_IMAGE_PURPOSE.XINSTALL])
        make_usable_osystem(self, os_name)
        compose_preseed_mock = self.patch(osystem, 'compose_preseed')
        compose_preseed_mock.side_effect = NoSuchOperatingSystem
        node = factory.make_Node(
            osystem=os_name, status=NODE_STATUS.READY)
        node.nodegroup.accept()
        self.useFixture(RunningClusterRPCFixture())
        self.assertRaises(
            NoSuchOperatingSystem,
            compose_preseed, PRESEED_TYPE.CURTIN, node)

    def test_compose_preseed_propagates_NoConnectionsAvailable(self):
        # If the region does not have any connections to the node's cluster
        # controller, compose_preseed() simply passes the exception up.
        os_name = factory.make_name('os')
        make_osystem(self, os_name, [BOOT_IMAGE_PURPOSE.XINSTALL])
        make_usable_osystem(self, os_name)
        node = factory.make_Node(
            osystem=os_name, status=NODE_STATUS.READY)
        self.assertRaises(
            NoConnectionsAvailable,
            compose_preseed, PRESEED_TYPE.CURTIN, node)
