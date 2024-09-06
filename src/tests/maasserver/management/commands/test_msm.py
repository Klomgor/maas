# Copyright 2024 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).
from datetime import datetime
import io

from django.core.management.base import CommandError
import pytest

# need to mock two different 'msm' modules, avoid conflict
from maasserver import msm as connector_service
from maasserver.management.commands import msm
from maasserver.secrets import SecretManager

# actual, expired token generated by MSM for enrolment
SAMPLE_JWT_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJiOWMyMDQ4YS1lMmMyLTQxY2MtODExMy1iZDRiMDZjN2RjY"
    "TYiLCJpc3MiOiI2M2VmM2FhNC0zNjY5LTQ3NzYtOTczMS03MzEzODI2MG"
    "E5MmEiLCJpYXQiOjE3MTMzMDA3ODMsImV4cCI6MTcxMzMwNDM4MywiYXV"
    "kIjpbInNpdGUiXSwicHVycG9zZSI6ImVucm9sbWVudCIsImVucm9sbWVu"
    "dC11cmwiOiJodHRwczovL21zbS9zaXRlL3YxL2Vucm9sbCJ9."
    "Kp0p0-KpB6lkskh8YLUTNiCyfmjLf-IFOgOh5XJUiVo"
)

# for mocking, since expired tokens won't be decoded
SAMPLE_JWT_PAYLOAD = {
    "sub": "b9c2048a-e2c2-41cc-8113-bd4b06c7dca6",
    "iss": "63ef3aa4-3669-4776-9731-73138260a92a",
    "iat": 1713300783,
    "exp": 1713304383,
    "aud": ["site"],
    "purpose": "enrolment",
    "service-url": "https://msm/ingress/",
}

YAML_CONFIG = """
metadata:
  latitude: 40.05275079137782
  longitude: -107.17401328725524
  note: 'super awesome site'
  country: US
  city: Town
  state: AK
  address: 123 Fake St.
  postal_code: '80205'
  timezone: US/Denver
"""


@pytest.fixture
def msm_mock(mocker):
    mocker.patch.object(msm, "get_cert_verify_msg", return_value="")
    yield mocker.patch.object(msm, "msm_enrol")


@pytest.mark.usefixtures("maasdb")
class TestMSM:
    def _configure_kwargs(
        self, command=msm.Command.ENROL_COMMAND, token=SAMPLE_JWT_TOKEN
    ) -> dict:
        return {
            "command": command,
            "enrolment_token": token,
            "config_file": io.TextIOWrapper(
                io.BytesIO(YAML_CONFIG.encode("utf-8")), encoding="utf-8"
            ),
        }

    def test_enrol_no(self, mocker, msm_mock):
        mocker.patch.object(msm, "prompt_yes_no", return_value=False)
        mocker.patch.object(msm.jwt, "decode", return_value=SAMPLE_JWT_PAYLOAD)
        opts = self._configure_kwargs()
        msm.Command().handle(**opts)
        msm_mock.assert_not_called()

    def test_enrol_yes(self, mocker, msm_mock):
        mocker.patch.object(msm, "prompt_yes_no", return_value=True)
        mocker.patch.object(msm.jwt, "decode", return_value=SAMPLE_JWT_PAYLOAD)
        opts = self._configure_kwargs()
        msm.Command().handle(**opts)
        msm_mock.assert_called_once_with(
            opts["enrolment_token"], metainfo=YAML_CONFIG
        )

    def test_enrol_expired_token(self, msm_mock):
        opts = self._configure_kwargs()
        with pytest.raises(CommandError, match="Enrolment token is expired"):
            msm.Command().handle(**opts)

    def test_bogus_token(self, msm_mock):
        opts = self._configure_kwargs()
        opts["enrolment_token"] = "not.a.token"
        with pytest.raises(CommandError, match="Invalid enrolment token"):
            msm.Command().handle(**opts)

    def test_enrol_no_config(self, mocker, msm_mock):
        mocker.patch.object(msm, "prompt_yes_no", return_value=True)
        mocker.patch.object(msm.jwt, "decode", return_value=SAMPLE_JWT_PAYLOAD)
        opts = self._configure_kwargs()
        opts["config_file"] = None
        msm.Command().handle(**opts)
        msm_mock.assert_called_once_with(opts["enrolment_token"], metainfo="")

    def test_enrol_extra_field(self, mocker, msm_mock):
        mocker.patch.object(msm.jwt, "decode", return_value=SAMPLE_JWT_PAYLOAD)
        mocker.patch.object(msm, "prompt_yes_no", return_value=False)
        opts = self._configure_kwargs()
        new_cfg = YAML_CONFIG + "extra: 'field'"
        opts["config_file"] = io.TextIOWrapper(
            io.BytesIO(new_cfg.encode("utf-8")), encoding="utf-8"
        )
        with pytest.raises(CommandError, match="Invalid config file"):
            msm.Command().handle(**opts)

    def test_enrol_config_missing_header(self, mocker, msm_mock):
        mocker.patch.object(msm.jwt, "decode", return_value=SAMPLE_JWT_PAYLOAD)
        mocker.patch.object(msm, "prompt_yes_no", return_value=False)
        opts = self._configure_kwargs()
        # remove the first line from the config file
        bad_cfg = "\n".join(YAML_CONFIG.split("\n")[2:])
        opts["config_file"] = io.TextIOWrapper(
            io.BytesIO(bad_cfg.encode("utf-8")), encoding="utf-8"
        )
        with pytest.raises(CommandError, match="Invalid config file"):
            msm.Command().handle(**opts)

    def test_enrol_bad_config_format(self, mocker, msm_mock):
        mocker.patch.object(msm.jwt, "decode", return_value=SAMPLE_JWT_PAYLOAD)
        mocker.patch.object(msm, "prompt_yes_no", return_value=False)
        opts = self._configure_kwargs()
        # remove the value from the last entry in the config file
        bad_cfg = ":".join(YAML_CONFIG.split(":")[:-1]) + ":"
        opts["config_file"] = io.TextIOWrapper(
            io.BytesIO(bad_cfg.encode("utf-8")), encoding="utf-8"
        )
        with pytest.raises(CommandError, match="Invalid config file"):
            msm.Command().handle(**opts)

    def test_enrol_wrong_config_type(self, mocker, msm_mock):
        mocker.patch.object(msm.jwt, "decode", return_value=SAMPLE_JWT_PAYLOAD)
        mocker.patch.object(msm, "prompt_yes_no", return_value=False)
        opts = self._configure_kwargs()
        bad_cfg = YAML_CONFIG.replace(
            "40.05275079137782", "'40.05275079137782'"
        )
        opts["config_file"] = io.TextIOWrapper(
            io.BytesIO(bad_cfg.encode("utf-8")), encoding="utf-8"
        )
        with pytest.raises(CommandError, match="Invalid config file"):
            msm.Command().handle(**opts)

    def test_status_waiting(self, mocker, capfd):
        mocker.patch.object(
            SecretManager, "get_composite_secret", return_value=None
        )
        opts = self._configure_kwargs(command=msm.Command.STATUS_COMMAND)
        msm.Command().handle(**opts)
        out, _ = capfd.readouterr()
        assert "No enrolment is in progress" in out

    def test_status_not_enroled(self, mocker, capfd):
        expected_started = datetime.now()
        expected_url = "http://test-maas.dev"
        secret = {
            "url": expected_url,
            "jwt": "",
        }
        mocker.patch.object(
            SecretManager, "get_composite_secret", return_value=secret
        )
        mocker.patch.object(
            connector_service,
            "_query_pending",
            return_value=(True, expected_started),
        )
        opts = self._configure_kwargs(command=msm.Command.STATUS_COMMAND)
        msm.Command().handle(**opts)
        out, _ = capfd.readouterr()
        assert (
            f"Enrolment with test-maas.dev is pending approval as of {expected_started.isoformat()}"
            in out
        )

    def test_status_enroled(self, mocker, capfd):
        expected_started = datetime.now()
        expected_url = "http://test-maas.dev"
        secret = {
            "url": expected_url,
            "jwt": "test.jwt.token",
        }
        mocker.patch.object(
            SecretManager, "get_composite_secret", return_value=secret
        )
        mocker.patch.object(
            connector_service,
            "_query_workflow",
            return_value=(True, expected_started),
        )
        mocker.patch.object(
            connector_service,
            "_query_pending",
            return_value=(False, expected_started),
        )
        opts = self._configure_kwargs(command=msm.Command.STATUS_COMMAND)
        msm.Command().handle(**opts)
        out, _ = capfd.readouterr()
        assert (
            f"Enroled with test-maas.dev as of {expected_started.isoformat()}"
            in out
        )
