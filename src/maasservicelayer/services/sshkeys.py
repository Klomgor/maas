# Copyright 2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import asyncio
from dataclasses import dataclass
from itertools import chain
import os
import ssl

import aiofiles
from aiohttp import ClientSession
from aiohttp.client import TCPConnector

from maascommon.constants import SYSTEM_CA_FILE
from maascommon.enums.sshkeys import (
    OPENSSH_PROTOCOL2_KEY_TYPES,
    SshKeysProtocolType,
)
from maasservicelayer.builders.sshkeys import SshKeyBuilder
from maasservicelayer.context import Context
from maasservicelayer.db.filters import QuerySpec
from maasservicelayer.db.repositories.sshkeys import (
    SshKeyClauseFactory,
    SshKeysRepository,
)
from maasservicelayer.exceptions.catalog import (
    AlreadyExistsException,
    BaseExceptionDetail,
    ValidationException,
)
from maasservicelayer.exceptions.constants import (
    UNIQUE_CONSTRAINT_VIOLATION_TYPE,
)
from maasservicelayer.models.sshkeys import SshKey
from maasservicelayer.services.base import BaseService, Service, ServiceCache


@dataclass(slots=True)
class SshKeysServiceCache(ServiceCache):
    session: ClientSession | None = None

    async def close(self) -> None:
        if self.session:
            await self.session.close()


class SshKeysService(BaseService[SshKey, SshKeysRepository, SshKeyBuilder]):
    def __init__(
        self,
        context: Context,
        sshkeys_repository: SshKeysRepository,
        cache: SshKeysServiceCache | None = None,
    ):
        super().__init__(context, sshkeys_repository, cache)
        self._session = None

    async def update_by_id(self, id, builder, etag_if_match=None):
        raise NotImplementedError("Update is not supported for ssh keys")

    async def update_many(self, query, builder):
        raise NotImplementedError("Update is not supported for ssh keys")

    async def update_one(self, query, builder, etag_if_match=None):
        raise NotImplementedError("Update is not supported for ssh keys")

    async def _update_resource(
        self, existing_resource, builder, etag_if_match=None
    ):
        raise NotImplementedError("Update is not supported for ssh keys")

    async def pre_create_hook(self, builder: SshKeyBuilder) -> None:
        # TODO: remove type ignore after implementing safe get for builders
        builder.key = await self.normalize_openssh_public_key(builder.key)  # type: ignore

        # skip the validation if it's a key imported by LP or GH.
        if builder.protocol is not None:
            return

        ssh_key_exists = await self.exists(
            query=QuerySpec(
                where=SshKeyClauseFactory.and_clauses(
                    [
                        SshKeyClauseFactory.with_key(builder.key),
                        # TODO: remove type ignore after implementing safe get for builders
                        SshKeyClauseFactory.with_user_id(builder.user_id),  # type: ignore
                    ]
                )
            )
        )
        if ssh_key_exists:
            raise AlreadyExistsException(
                details=[
                    BaseExceptionDetail(
                        type=UNIQUE_CONSTRAINT_VIOLATION_TYPE,
                        message="An SSH key with such identifiers already exist.",
                    )
                ]
            )

    @staticmethod
    def build_cache_object() -> SshKeysServiceCache:
        return SshKeysServiceCache()

    @Service.from_cache_or_execute("session")
    async def _get_session(self) -> ClientSession:
        context = ssl.create_default_context(cafile=SYSTEM_CA_FILE)
        tcp_conn = TCPConnector(ssl=context)
        return ClientSession(trust_env=True, connector=tcp_conn)

    async def import_keys(
        self, protocol: SshKeysProtocolType, auth_id: str, user_id: int
    ) -> list[SshKey]:
        imported_keys = []
        match protocol:
            case SshKeysProtocolType.LP:
                keys = await self._get_ssh_key_from_launchpad(auth_id)
            case SshKeysProtocolType.GH:
                keys = await self._get_ssh_key_from_github(auth_id)
            case _:
                raise ValueError(
                    f"Unknwon protocol {protocol}. Valid protocols are 'LP' and 'GH'."
                )
        if not keys:
            raise ValidationException.build_for_field(
                field="auth_id",
                message=f"Unable to import SSH keys. There are no SSH keys for {protocol.value} user {auth_id}.",
            )

        existing_keys = await self.get_many(
            query=QuerySpec(
                where=SshKeyClauseFactory.and_clauses(
                    [
                        SshKeyClauseFactory.with_user_id(user_id),
                        SshKeyClauseFactory.with_protocol(protocol),
                        SshKeyClauseFactory.with_auth_id(auth_id),
                    ]
                )
            )
        )

        existing_keys_values = [k.key for k in existing_keys]
        for key in keys:
            if key not in existing_keys_values:
                builder = SshKeyBuilder(
                    key=key,
                    protocol=protocol,
                    auth_id=auth_id,
                    user_id=user_id,
                )
                imported_keys.append(await self.create(builder))

        imported_keys.extend(existing_keys)
        return imported_keys

    async def _get_ssh_key_from_launchpad(self, auth_id: str) -> list[str]:
        url = f"https://launchpad.net/~{auth_id}/+sshkeys"
        session = await self._get_session()
        response = await session.get(url)
        # Check for 404 error which happens for an unknown user or 410 for page gone.
        if response.status in (404, 410):
            raise ValidationException.build_for_field(
                field="auth_id",
                message=f"Unable to import SSH keys. Launchpad user {auth_id} doesn't exist.",
            )
        response.raise_for_status()
        text_response = (await response.text()).splitlines()
        return [key for key in text_response if key]

    async def _get_ssh_key_from_github(self, auth_id: str) -> list[str]:
        url = f"https://api.github.com/users/{auth_id}/keys"
        session = await self._get_session()
        response = await session.get(url)
        # Check for 404 error which happens for an unknown user or 410 for page gone.
        if response.status in (404, 410):
            raise ValidationException.build_for_field(
                field="auth_id",
                message=f"Unable to import SSH keys. Github user {auth_id} doesn't exist.",
            )
        response.raise_for_status()
        # github returns JSON content
        json_response = await response.json()
        return [data["key"] for data in json_response if "key" in data]

    async def normalize_openssh_public_key(self, key: str) -> str:
        """Validate and normalise an OpenSSH public key.

        Essentially: ensure we have a public key first (and not try to extract a
        public key from a private key) and then pump it through an ssh-keygen(1)
        pipeline to ensure it's valid.

        sshd(8) has a section describing the format of ~/.ssh/authorized_keys:

          Each line of the file contains one key (empty lines and lines starting
          with a ‘#’ are ignored as comments). Protocol 1 public keys consist of
          the following space-separated fields: options, bits, exponent, modulus,
          comment. Protocol 2 public key consist of: options, keytype,
          base64-encoded key, comment. The options field is optional; [...]. The
          bits, exponent, modulus, and comment fields give the RSA key for
          protocol version 1; the comment field is not used for anything (but may
          be convenient for the user to identify the key). For protocol version 2
          the keytype is “ecdsa-sha2-nistp256”, “ecdsa-sha2-nistp384”,
          “ecdsa-sha2-nistp521”, “ssh-ed25519”, “ssh-dss” or “ssh-rsa”.

        ssh-keygen(1) explicitly recommends appending public key files to
        ~/.ssh/authorized_keys:

          The contents ... should be added to ~/.ssh/authorized_keys on all
          machines where the user wishes to log in using public key
          authentication.

        Marrying the two we have official documentation for the format of public
        key files!

        We should ignore protocol 1 keys. It does not even appear to be possible
        to create an rsa1 key on Xenial:

          $ ssh-keygen -t rsa1
          Generating public/private rsa1 key pair.
          Enter file in which to save the key (.../.ssh/identity):
          Enter passphrase (empty for no passphrase):
          Enter same passphrase again:
          Saving key ".../.ssh/identity" failed: unknown or unsupported key type

        Although ~/.ssh/authorized_keys can contain options, we should assume that
        the public keys pasted into MAAS do not have options. Public key files
        generated by ssh-keygen(1) will not contain options.

        Given all that, this function does the following:

        1. Checks there are 2 or more fields: keytype base64-encoded-key [comment]
        (the comment can contain whitespace).

        2. Checks that keytype is one of “ssh-dss”, “ssh-rsa”, “ssh-ed25519”,
        “ecdsa-sha2-nistp256”, “ecdsa-sha2-nistp384”, or “ecdsa-sha2-nistp521”,

        2. Run through `ssh-keygen -e -f $keyfile > $intermediate <&-`.

        3. Run through `ssh-keygen -i -f $intermediate > $pubkey <&-`.

        4. $pubkey should contain two fields: keytype, base64-encoded key.

        5. Reunite $pubkey with comment, if there was one.

        Errors from ssh-keygen(1) at any point should be reported *with the error
        message*. Previously all errors relating to SSH keys were coalesced into
        the same static message.

        """

        parts = key.split()
        if len(parts) >= 2:
            keytype, key, *comments = parts
        else:
            raise ValidationException.build_for_field(
                field="key",
                message=f"Key should contain 2 or more space separated parts (key type, base64-encoded key, optional comments), not {len(parts)})",
            )
        if keytype not in OPENSSH_PROTOCOL2_KEY_TYPES:
            raise ValidationException.build_for_field(
                field="key",
                message=f"Key type {keytype} not recognised; it should be one of: {' '.join(sorted(OPENSSH_PROTOCOL2_KEY_TYPES))}",
            )
        env = dict(os.environ)
        # Request OpenSSH to use /bin/true when prompting for passwords. We also
        # have to redirect stdin from, say, /dev/null so that it doesn't use the
        # terminal (when this is executed from a terminal).
        env["SSH_ASKPASS"] = "/bin/true"
        async with aiofiles.tempfile.NamedTemporaryFile("wb") as keyfile:
            await keyfile.write(f"{keytype} {key}".encode("utf-8"))
            await keyfile.seek(0)
            # Convert given key to RFC4716 form.
            proc = await asyncio.create_subprocess_shell(
                f"ssh-keygen -e -f {keyfile.name}",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
            rfc4716key, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise ValidationException.build_for_field(
                    field="key",
                    message=f"Key could not be converted to RFC4716 form. Stderr: {stderr}",
                )
            await keyfile.write(rfc4716key)
            await keyfile.seek(0)
            # Convert RFC4716 back to OpenSSH format public key.
            proc = await asyncio.create_subprocess_shell(
                f"ssh-keygen -i -f {keyfile.name}",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
            opensshkey, stderr = await proc.communicate()
            if proc.returncode != 0:
                # If this happens it /might/ be an OpenSSH bug. If we've managed
                # to convert to RFC4716 form then it seems reasonable to assume
                # that OpenSSH has already given this key its blessing.
                raise ValidationException.build_for_field(
                    field="key",
                    message=f"Key could not be converted from RFC4716 form to OpenSSH public key form. Stderr: {stderr}",
                )
            keytype, key = opensshkey.decode("utf-8").split()
        return " ".join(chain((keytype, key), comments))
