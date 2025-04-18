# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for common RPC code."""

import random
import re
from unittest.mock import ANY, sentinel

from testtools.monkey import MonkeyPatcher
from twisted.internet.defer import Deferred, inlineCallbacks
from twisted.internet.protocol import connectionDone
from twisted.internet.testing import StringTransport
from twisted.protocols import amp
from twisted.python.failure import Failure

from maastesting import get_testing_timeout
from maastesting.factory import factory
from maastesting.runtest import MAASTwistedRunTest
from maastesting.testcase import MAASTestCase
from maastesting.twisted import (
    always_fail_with,
    extract_result,
    TwistedLoggerFixture,
)
from provisioningserver.prometheus.metrics import PROMETHEUS_METRICS
from provisioningserver.rpc import common
from provisioningserver.rpc.common import (
    ConnectionAuthStatus,
    RPCUnauthorizedException,
)
from provisioningserver.rpc.testing.doubles import (
    DummyConnection,
    FakeConnection,
    FakeConnectionToRegion,
)


class TestClient(MAASTestCase):
    def setUp(self):
        super().setUp()
        self.command = sentinel.command
        self.command.__name__ = "Command"  # needed for metrics labels

    def test_init(self):
        conn = DummyConnection()
        client = common.Client(conn)
        self.assertIs(client._conn, conn)

    def make_connection_and_client(self):
        conn = FakeConnectionToRegion()
        client = common.Client(conn)
        return conn, client

    def test_ident(self):
        conn, client = self.make_connection_and_client()
        conn.ident = self.getUniqueString()
        self.assertEqual(conn.ident, client.ident)

    def test_localIdent(self):
        conn, client = self.make_connection_and_client()
        conn.localIdent = self.getUniqueString()
        self.assertEqual(conn.localIdent, client.localIdent)

    def test_localIdent_for_IConnection(self):
        conn = FakeConnection()
        client = common.Client(conn)
        with self.assertRaisesRegex(
            NotImplementedError, ".* only available in the rack\\b"
        ):
            client.localIdent  # noqa: B018

    def test_address(self):
        conn, client = self.make_connection_and_client()
        conn.address = self.getUniqueString()
        self.assertEqual(conn.address, client.address)

    def test_address_for_IConnection(self):
        conn = FakeConnection()
        client = common.Client(conn)
        with self.assertRaisesRegex(
            NotImplementedError, ".* only available in the rack\\b"
        ):
            client.address  # noqa: B018

    def test_call_no_timeout(self):
        conn, client = self.make_connection_and_client()
        self.patch_autospec(conn, "callRemote")
        conn.callRemote.return_value = sentinel.response
        response = client(
            sentinel.command, _timeout=None, foo=sentinel.foo, bar=sentinel.bar
        )
        self.assertIs(response, sentinel.response)
        conn.callRemote.assert_called_once_with(
            sentinel.command, foo=sentinel.foo, bar=sentinel.bar
        )

    def test_call_zero_timeout(self):
        conn, client = self.make_connection_and_client()
        self.patch_autospec(conn, "callRemote")
        conn.callRemote.return_value = sentinel.response
        response = client(
            sentinel.command, _timeout=0, foo=sentinel.foo, bar=sentinel.bar
        )
        self.assertIs(response, sentinel.response)
        conn.callRemote.assert_called_once_with(
            sentinel.command, foo=sentinel.foo, bar=sentinel.bar
        )

    def test_call_default_timeout(self):
        conn, client = self.make_connection_and_client()
        self.patch_autospec(common, "deferWithTimeout")
        common.deferWithTimeout.return_value = sentinel.response
        response = client(sentinel.command, foo=sentinel.foo, bar=sentinel.bar)
        self.assertIs(response, sentinel.response)
        common.deferWithTimeout.assert_called_once_with(
            120,
            conn.callRemote,
            sentinel.command,
            foo=sentinel.foo,
            bar=sentinel.bar,
        )

    def test_call_custom_timeout(self):
        conn, client = self.make_connection_and_client()
        timeout = random.randint(10, 20)
        self.patch_autospec(common, "deferWithTimeout")
        common.deferWithTimeout.return_value = sentinel.response
        response = client(
            sentinel.command,
            _timeout=timeout,
            foo=sentinel.foo,
            bar=sentinel.bar,
        )
        self.assertIs(response, sentinel.response)
        common.deferWithTimeout.assert_called_once_with(
            timeout,
            conn.callRemote,
            sentinel.command,
            foo=sentinel.foo,
            bar=sentinel.bar,
        )

    def test_call_with_keyword_arguments_raises_useful_error(self):
        conn = DummyConnection()
        client = common.Client(conn)
        expected_message = re.escape(
            "provisioningserver.rpc.common.Client called with 3 positional "
            "arguments, (1, 2, 3), but positional arguments are not "
            "supported. Usage: client(command, arg1=value1, ...)"
        )
        with self.assertRaisesRegex(TypeError, expected_message):
            client(sentinel.command, 1, 2, 3)

    def test_call_records_latency_metric(self):
        mock_metrics = self.patch(PROMETHEUS_METRICS, "update")
        conn, client = self.make_connection_and_client()
        self.patch_autospec(conn, "callRemote")
        conn.callRemote.return_value = sentinel.response
        client(
            sentinel.command, _timeout=None, foo=sentinel.foo, bar=sentinel.bar
        )
        mock_metrics.assert_called_with(
            "maas_rack_region_rpc_call_latency",
            "observe",
            labels={"call": "Command"},
            value=ANY,
        )

    def test_getHostCertificate(self):
        conn, client = self.make_connection_and_client()
        conn.hostCertificate = sentinel.hostCertificate
        self.assertIs(client.getHostCertificate(), sentinel.hostCertificate)

    def test_getPeerCertificate(self):
        conn, client = self.make_connection_and_client()
        conn.peerCertificate = sentinel.peerCertificate
        self.assertIs(client.getPeerCertificate(), sentinel.peerCertificate)

    def test_isSecure(self):
        conn, client = self.make_connection_and_client()
        conn.peerCertificate = sentinel.peerCertificate
        self.assertTrue(client.isSecure())

    def test_isSecure_not(self):
        conn, client = self.make_connection_and_client()
        conn.peerCertificate = None
        self.assertFalse(client.isSecure())

    def test_eq__(self):
        conn, client = self.make_connection_and_client()
        self.assertEqual(client, client)
        client_for_same_connection = common.Client(conn)
        self.assertEqual(client_for_same_connection, client)
        _, client_for_another_connection = self.make_connection_and_client()
        self.assertNotEqual(client_for_another_connection, client)

    def test_hash__(self):
        conn, client = self.make_connection_and_client()
        # The hash of a common.Client object is that of its connection.
        self.assertEqual(hash(client), hash(conn))


class TestRPCProtocol(MAASTestCase):
    def test_init(self):
        protocol = common.RPCProtocol()
        self.assertIsInstance(protocol.onConnectionMade, Deferred)
        self.assertFalse(protocol.onConnectionMade.called)
        self.assertIsInstance(protocol.onConnectionLost, Deferred)
        self.assertFalse(protocol.onConnectionLost.called)
        self.assertIsInstance(protocol, amp.AMP)

    def test_onConnectionMade_fires_when_connection_is_made(self):
        protocol = common.RPCProtocol()
        protocol.connectionMade()
        self.assertIsInstance(protocol.onConnectionMade, Deferred)
        self.assertTrue(protocol.onConnectionMade.called)

    def test_onConnectionLost_fires_when_connection_is_lost(self):
        protocol = common.RPCProtocol()
        protocol.makeConnection(StringTransport())
        protocol.connectionLost(connectionDone)
        self.assertIsInstance(protocol.onConnectionLost, Deferred)
        self.assertTrue(protocol.onConnectionLost.called)

    def test_unhandled_error_handler_closed(self):
        """
        Test the unhandledError method when the handler is closed.
        """
        amp_unhandledError = self.patch_autospec(amp.AMP, "unhandledError")

        protocol = common.RPCProtocol()

        fake_failure = Failure(
            RuntimeError(
                "unable to perform operation on <UVPoll closed=True 0x7f3506bd4c10>; the handler is closed"
            )
        )

        protocol.unhandledError(fake_failure)

        amp_unhandledError.assert_called_once()

    def test_unhandled_error_generic(self):
        """
        Test the unhandledError method for a generic failure.
        """
        amp_unhandledError = self.patch_autospec(amp.AMP, "unhandledError")

        protocol = common.RPCProtocol()

        fake_failure = Failure(Exception("Some generic error"))

        protocol.unhandledError(fake_failure)

        amp_unhandledError.assert_not_called()


class TestConnectionAuthStatus(MAASTestCase):
    def test_default_auth_status(self):
        auth_status = ConnectionAuthStatus()
        self.assertEqual(auth_status.is_authenticated, False)

    def test_set_is_authenticated(self):
        auth_status = ConnectionAuthStatus(True)
        self.assertEqual(auth_status.is_authenticated, True)
        auth_status.set_is_authenticated(False)
        self.assertEqual(auth_status.is_authenticated, False)


class TestSecuredRPCProtocol(MAASTestCase):
    run_tests_with = MAASTwistedRunTest.make_factory(
        timeout=get_testing_timeout()
    )

    def setUp(self):
        super().setUp()
        self.patcher = MonkeyPatcher()
        self.patcher.add_patch(
            #
            common.RPCProtocol,
            "dispatchCommand",
            (lambda *args, **kwargs: True),
        )

    @inlineCallbacks
    def test_unauthenticated_connection_is_dropped(self):
        protocol = common.SecuredRPCProtocol(
            unauthenticated_commands=[],
            auth_status=ConnectionAuthStatus(False),
        )
        protocol.makeConnection(StringTransport())

        seq = b"%d" % random.randrange(0, 2**32)
        cmd = factory.make_string().encode("ascii")
        box = amp.AmpBox(_ask=seq, _command=cmd)
        self.patcher.add_patch(
            # do not sleep. No need to slow down the tests
            common,
            "pause",
            (lambda *args, **kwargs: True),
        )
        self.patcher.patch()
        try:
            with self.assertRaisesRegex(
                RPCUnauthorizedException,
                "The RPC command requires authentication. ",
            ):
                yield protocol.dispatchCommand(box)
        finally:
            self.patcher.restore()

    @inlineCallbacks
    def test_authenticated_connection(self):
        protocol = common.SecuredRPCProtocol(
            unauthenticated_commands=[], auth_status=ConnectionAuthStatus(True)
        )
        protocol.makeConnection(StringTransport())

        seq = b"%d" % random.randrange(0, 2**32)
        cmd = factory.make_string().encode("ascii")
        box = amp.AmpBox(_ask=seq, _command=cmd)

        self.patcher.patch()
        try:
            result = yield protocol.dispatchCommand(box)
            self.assertEqual(result, True)
        finally:
            self.patcher.restore()

    @inlineCallbacks
    def test_unauthenticated_command(self):
        cmd = factory.make_string()
        protocol = common.SecuredRPCProtocol(
            unauthenticated_commands=[cmd.encode("ascii")],
            auth_status=ConnectionAuthStatus(False),
        )
        protocol.makeConnection(StringTransport())

        seq = b"%d" % random.randrange(0, 2**32)
        box = amp.AmpBox(_ask=seq, _command=cmd.encode("ascii"))

        self.patcher.patch()
        try:
            result = yield protocol.dispatchCommand(box)
            self.assertEqual(result, True)
        finally:
            self.patcher.restore()

    @inlineCallbacks
    def test_connection_is_trusted_after_retries(self):
        cmd = factory.make_string()
        auth_status = ConnectionAuthStatus(False)
        protocol = common.SecuredRPCProtocol(
            unauthenticated_commands=[],
            auth_status=auth_status,
        )
        protocol.makeConnection(StringTransport())

        seq = b"%d" % random.randrange(0, 2**32)
        box = amp.AmpBox(_ask=seq, _command=cmd.encode("ascii"))

        class Pauser:
            def __init__(self):
                self.retry = 0
                self.calls = []

            def mock(self, time):
                self.retry += 1
                # At the last try, simulate that the connection become trusted
                if self.retry == 4:
                    auth_status.set_is_authenticated(True)
                self.calls.append(time)

        pauser = Pauser()

        self.patcher.add_patch(
            # Do not sleep and mock the connection status change.
            common,
            "pause",
            (lambda time: pauser.mock(time)),
        )
        self.patcher.patch()
        try:
            result = yield protocol.dispatchCommand(box)
            self.assertEqual(result, True)
            self.assertEqual(pauser.retry, 4)
            # exponential backoff
            self.assertEqual(pauser.calls, [0.0, 0.5, 1.5, 3.5])
        finally:
            self.patcher.restore()


class TestRPCProtocol_UnhandledErrorsWhenHandlingResponses(MAASTestCase):
    answer_seq = b"%d" % random.randrange(0, 2**32)
    answer_box = amp.AmpBox(_answer=answer_seq)

    error_seq = b"%d" % random.randrange(0, 2**32)
    error_box = amp.AmpBox(
        _error=error_seq,
        _error_code=amp.UNHANDLED_ERROR_CODE,
        _error_description=factory.make_string(),
    )

    scenarios = (
        ("_answerReceived", {"seq": answer_seq, "box": answer_box}),
        ("_errorReceived", {"seq": error_seq, "box": error_box}),
    )

    def test_unhandled_errors_logged_and_do_not_cause_disconnection(self):
        self.patch(common.log, "debug")
        protocol = common.RPCProtocol()
        protocol.makeConnection(StringTransport())
        # Poke a request into the dispatcher that will always fail.
        d = Deferred().addCallback(lambda _: 0 / 0)
        protocol._outstandingRequests[self.seq] = d
        # Push a box in response to the request.
        with TwistedLoggerFixture() as logger:
            protocol.ampBoxReceived(self.box)
        # The Deferred does not have a dangling error.
        self.assertIsNone(extract_result(d))
        # The transport is still connected.
        self.assertFalse(protocol.transport.disconnecting)
        # The error has been logged.
        self.assertIn(
            (
                "Unhandled failure during AMP request. This is probably a bug. "
                "Please ensure that this error is handled within application code.\n"
                "Traceback (most recent call last):"
            ),
            logger.output,
        )


class TestRPCProtocol_UnhandledErrorsWhenHandlingCommands(MAASTestCase):
    def test_unhandled_errors_do_not_cause_disconnection(self):
        self.patch(common.log, "debug")
        protocol = common.RPCProtocol()
        protocol.makeConnection(StringTransport())
        # Ensure that the superclass dispatchCommand() will fail.
        dispatchCommand = self.patch(amp.AMP, "dispatchCommand")
        dispatchCommand.side_effect = always_fail_with(ZeroDivisionError())
        # Push a command box into the protocol.
        seq = b"%d" % random.randrange(0, 2**32)
        cmd = factory.make_string().encode("ascii")
        box = amp.AmpBox(_ask=seq, _command=cmd)
        with TwistedLoggerFixture() as logger:
            protocol.ampBoxReceived(box)
        # The transport is still connected.
        self.assertFalse(protocol.transport.disconnecting)
        # The error has been logged on the originating side of the AMP
        # session, along with an explanatory message. The message includes a
        # command reference.
        cmd_ref = common.make_command_ref(box)
        self.assertIn(
            (
                "Unhandled failure dispatching AMP command. This is probably a bug. "
                "Please ensure that this error is handled within application code or "
                f"declared in the signature of the {cmd} command. [{cmd_ref}]\n"
                "Traceback (most recent call last):"
            ),
            logger.output,
        )
        # A simpler error message has been transmitted over the wire. It
        # includes the same command reference as logged locally.
        protocol.transport.io.seek(0)
        observed_boxes_sent = amp.parse(protocol.transport.io)
        expected_boxes_sent = [
            amp.AmpBox(
                _error=seq,
                _error_code=amp.UNHANDLED_ERROR_CODE,
                _error_description=(
                    b"Unknown Error [%s]" % cmd_ref.encode("ascii")
                ),
            )
        ]
        self.assertEqual(expected_boxes_sent, observed_boxes_sent)


class TestMakeCommandRef(MAASTestCase):
    """Tests for `common.make_command_ref`."""

    def test_command_ref_includes_host_pid_command_and_ask_sequence(self):
        host = factory.make_name("hostname")
        pid = random.randint(99, 9999999)
        cmd = factory.make_name("command").encode("ascii")
        ask = str(random.randint(99, 9999999)).encode("ascii")
        box = amp.AmpBox(_command=cmd, _ask=ask)

        self.patch(common, "gethostname").return_value = host
        self.patch(common, "getpid").return_value = pid

        self.assertEqual(
            common.make_command_ref(box),
            "%s:pid=%s:cmd=%s:ask=%s"
            % (host, pid, cmd.decode("ascii"), ask.decode("ascii")),
        )

    def test_replaces_missing_ask_with_none(self):
        box = amp.AmpBox(_command=b"command")

        self.patch(common, "gethostname").return_value = "host"
        self.patch(common, "getpid").return_value = 1234

        self.assertEqual(
            "host:pid=1234:cmd=command:ask=none", common.make_command_ref(box)
        )
