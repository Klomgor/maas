# Copyright 2014-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from django.core.exceptions import ValidationError
from django.db import IntegrityError

from maasserver.models import SSLKey
from maasserver.models.sslkey import validate_ssl_key
from maasserver.testing import get_data
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase


class TestSSLKeyValidator(MAASServerTestCase):
    def test_validates_x509_public_key(self):
        key_string = get_data("data/test_x509_0.pem")
        validate_ssl_key(key_string)
        # No ValidationError.

    def test_does_not_validate_random_data(self):
        key_string = factory.make_string()
        self.assertRaises(ValidationError, validate_ssl_key, key_string)


class TestSSLKey(MAASServerTestCase):
    def test_sslkey_validation_with_valid_key(self):
        key_string = get_data("data/test_x509_0.pem")
        user = factory.make_User()
        key = SSLKey(key=key_string, user=user)
        key.full_clean()
        # No ValidationError.

    def test_sslkey_validation_fails_if_key_is_invalid(self):
        key_string = factory.make_string()
        user = factory.make_User()
        key = SSLKey(key=key_string, user=user)
        self.assertRaises(ValidationError, key.full_clean)

    def test_sslkey_user_and_key_unique_together(self):
        key_string = get_data("data/test_x509_0.pem")
        user = factory.make_User()
        key = SSLKey(key=key_string, user=user)
        key.save()
        key2 = SSLKey(key=key_string, user=user)
        self.assertRaises(ValidationError, key2.full_clean)

    def test_sslkey_user_and_key_unique_together_db_level(self):
        # Even if we hack our way around model-level checks, uniqueness
        # of the user/key combination is enforced at the database level.
        key_string = get_data("data/test_x509_0.pem")
        user = factory.make_User()
        existing_key = SSLKey(key=key_string, user=user)
        existing_key.save()
        # The trick to hack around the model-level checks: create a
        # duplicate key for another user, then attach it to the same
        # user as the existing key by updating it directly in the
        # database.
        redundant_key = SSLKey(key=key_string, user=factory.make_User())
        redundant_key.save()
        self.assertRaises(
            IntegrityError,
            SSLKey.objects.filter(id=redundant_key.id).update,
            user=user,
        )

    def test_sslkey_same_key_can_be_used_by_different_users(self):
        key_string = get_data("data/test_x509_0.pem")
        user = factory.make_User()
        key = SSLKey(key=key_string, user=user)
        key.save()
        user2 = factory.make_User()
        key2 = SSLKey(key=key_string, user=user2)
        key2.full_clean()
        # No ValidationError.


class TestSSLKeyManager(MAASServerTestCase):
    def test_get_keys_for_user_no_keys(self):
        user = factory.make_User()
        keys = SSLKey.objects.get_keys_for_user(user)
        self.assertCountEqual([], keys)

    def test_get_keys_for_user_with_keys(self):
        user1, created_keys = factory.make_user_with_ssl_keys(
            n_keys=3, username="user1"
        )
        # user2
        factory.make_user_with_ssl_keys(n_keys=2)
        keys = SSLKey.objects.get_keys_for_user(user1)
        self.assertCountEqual([key.key for key in created_keys], keys)
