# Copyright 2014-2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Model definition for StaticIPAddress.

Contains all the in-use static IP addresses that are allocated by MAAS.
Generally speaking, these are written out to the DHCP server as "host"
blocks which will tie MACs into a specific IP.  The IPs are separate
from the dynamic range that the DHCP server itself allocates to unknown
clients.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from queue import Empty, Queue
import threading
from typing import Dict, Iterable, Optional, Set, TypeVar

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import (
    CASCADE,
    DateTimeField,
    ForeignKey,
    GenericIPAddressField,
    IntegerField,
    Manager,
    PROTECT,
    Q,
    UniqueConstraint,
)
from netaddr import IPAddress

from maascommon.workflows.dhcp import (
    CONFIGURE_DHCP_WORKFLOW_NAME,
    ConfigureDHCPParam,
)
from maasserver import locks
from maasserver.enum import (
    INTERFACE_LINK_TYPE,
    INTERFACE_TYPE,
    IPADDRESS_TYPE,
    IPADDRESS_TYPE_CHOICES_DICT,
)
from maasserver.exceptions import (
    StaticIPAddressOutOfRange,
    StaticIPAddressUnavailable,
)
from maasserver.models.cleansave import CleanSave
from maasserver.models.subnet import Subnet
from maasserver.models.timestampedmodel import TimestampedModel
from maasserver.utils import orm
from maasserver.utils.orm import post_commit_do
from maasserver.workflow import start_workflow
from provisioningserver.utils.enum import map_enum_reverse

StaticIPAddress = TypeVar("StaticIPAddress")


def convert_leases_to_dict(leases):
    """Convert a list of leases to a dictionary.

    :param leases: list of (ip, mac) tuples discovered from the leases table.
    :return: dict of {ip: [mac,...], ...} leases.
    """
    ip_leases = defaultdict(list)
    for ip, mac in leases:
        ip_leases[ip].append(mac)
    return ip_leases


@dataclass
class SubnetAllocationQueue:
    pool: Queue[str] = field(default_factory=Queue)
    reserved: Set[str] = field(default_factory=set)
    pending: int = 0

    def get(self) -> str:
        return self.pool.get_nowait()

    def fill(self, addresses: Iterable[str]):
        for addr in addresses:
            self.pool.put(addr)

    def reserve(self, address: str):
        self.reserved.add(address)

    def free(self, address: str):
        self.reserved.remove(address)

    def get_reserved(self, extra: Iterable[str]) -> Iterable[str]:
        return self.reserved.union(extra)


class FreeIPAddress:
    pool: Dict[str, SubnetAllocationQueue] = {}
    counter_lock = threading.Lock()
    pool_lock = threading.Lock()

    def __init__(self, subnet: Subnet, exclude: Optional[Iterable] = None):
        self._subnet = subnet
        self._free_ip = None
        self._exclude = exclude or []
        with FreeIPAddress.pool_lock:
            self.queue = FreeIPAddress.pool.setdefault(
                str(subnet), SubnetAllocationQueue()
            )

    def __enter__(self) -> str:
        while self._free_ip is None:
            try:
                ip = self.queue.get()
            except Empty:
                self._fill_pool()
            else:
                if ip not in self._exclude:
                    self._free_ip = ip
        self.queue.reserve(self._free_ip)
        return self._free_ip

    def __exit__(self, *_):
        self.queue.free(self._free_ip)

    def _update_counter(self, adj: int):
        with FreeIPAddress.counter_lock:
            self.queue.pending += adj

    def _fill_pool(self):
        self._update_counter(1)
        with FreeIPAddress.pool_lock:
            pending = self.queue.pending
            assert pending >= 0
            if pending > 0:
                excl = self.queue.get_reserved(self._exclude)
                addresses = self._subnet.get_next_ip_for_allocation(
                    excl, count=pending
                )
                self.queue.fill(addresses)
                self._update_counter(-len(addresses))

    @classmethod
    def clean_cache(cls, subnet: Subnet):
        pass

    @classmethod
    def remove_cache(cls, subnet: Subnet):
        """Remove cache for this subnet"""
        with cls.pool_lock:
            cls.pool.pop(str(subnet), None)


class StaticIPAddressManager(Manager):
    """A utility to manage collections of IPAddresses."""

    def _verify_alloc_type(self, alloc_type, user=None):
        """Check validity of an `alloc_type` parameter when allocating.

        Also checks consistency with the `user` parameter.  If `user` is not
        `None`, then the allocation has to be `USER_RESERVED`, and vice versa.
        """
        if alloc_type not in [
            IPADDRESS_TYPE.AUTO,
            IPADDRESS_TYPE.STICKY,
            IPADDRESS_TYPE.USER_RESERVED,
        ]:
            raise ValueError(
                f"IP address type {alloc_type} is not allowed to use allocate_new."
            )

        if user is None:
            if alloc_type == IPADDRESS_TYPE.USER_RESERVED:
                raise AssertionError(
                    "Must provide user for USER_RESERVED alloc_type."
                )
        else:
            if alloc_type != IPADDRESS_TYPE.USER_RESERVED:
                raise AssertionError(
                    "Must not provide user for alloc_type other "
                    "than USER_RESERVED."
                )

    def _attempt_allocation(
        self, requested_address, alloc_type, user=None, subnet=None
    ) -> StaticIPAddress:
        """Attempt to allocate `requested_address`.

        All parameters must have been checked first.  This method relies on
        `IntegrityError` to detect addresses that are already in use, so
        nothing else must cause that error.

        Transaction model and isolation level have changed over time, and may
        do so again, so relying on database-level uniqueness validation is the
        most robust way we have of checking for clashes.

        :param requested_address: An `IPAddress` for the address that should
            be allocated.
        :param alloc_type: Allocation type.
        :param user: Optional user.
        :return: `StaticIPAddress` if successful.
        :raise StaticIPAddressUnavailable: if the address was already taken.
        """
        ipaddress = StaticIPAddress(alloc_type=alloc_type, subnet=subnet)
        try:
            # Try to save this address to the database. Do this in a nested
            # transaction so that we can continue using the outer transaction
            # even if this breaks.
            with transaction.atomic():
                ipaddress.set_ip_address(requested_address.format())
                ipaddress.save()
        except IntegrityError:
            # The address is already taken.
            raise StaticIPAddressUnavailable(  # noqa: B904
                f"The IP address {requested_address.format()} is already in use."
            )
        else:
            # We deliberately do *not* save the user until now because it
            # might result in an IntegrityError, and we rely on the latter
            # in the code above to indicate an already allocated IP
            # address and nothing else.
            ipaddress.user = user
            ipaddress.save()
            return ipaddress

    def _attempt_allocation_of_free_address(
        self, requested_address, alloc_type, user=None, subnet=None
    ) -> StaticIPAddress:
        """Attempt to allocate `requested_address`, which is known to be free.

        It is known to be free *in this transaction*, so this could still
        fail. If it does fail because of a `UNIQUE_VIOLATION` it will request
        a retry, except while holding an addition lock. This is not perfect:
        other threads could jump in before acquiring the lock and steal an
        apparently free address. However, in stampede situations this appears
        to be effective enough. Experiment by increasing the `count` parameter
        in `test_allocate_new_works_under_extreme_concurrency`.

        This method shares a lot in common with `_attempt_allocation` so check
        out its documentation for more details.

        :param requested_address: The address to be allocated.
        :typr requested_address: IPAddress
        :param alloc_type: Allocation type.
        :param user: Optional user.
        :return: `StaticIPAddress` if successful.
        :raise RetryTransaction: if the address was already taken.
        """
        ipaddress = StaticIPAddress(alloc_type=alloc_type, subnet=subnet)
        try:
            # Try to save this address to the database. Do this in a nested
            # transaction so that we can continue using the outer transaction
            # even if this breaks.
            with orm.savepoint():
                ipaddress.set_ip_address(requested_address.format())
                ipaddress.save()
        except IntegrityError as error:
            if orm.is_unique_violation(error):
                # The address is taken. We could allow the transaction retry
                # machinery to take care of this, but instead we'll ask it to
                # retry with the `address_allocation` lock. We can't take it
                # here because we're already in a transaction; we need to exit
                # the transaction, take the lock, and only then try again.
                orm.request_transaction_retry(locks.address_allocation)
            else:
                raise
        else:
            # We deliberately do *not* save the user until now because it
            # might result in an IntegrityError, and we rely on the latter
            # in the code above to indicate an already allocated IP
            # address and nothing else.
            ipaddress.user = user
            ipaddress.save()
            return ipaddress

    def allocate_new(
        self,
        subnet=None,
        alloc_type=IPADDRESS_TYPE.AUTO,
        user=None,
        requested_address=None,
        exclude_addresses=None,
        restrict_ip_to_unreserved_ranges: bool = True,
    ) -> StaticIPAddress:
        """Return a new StaticIPAddress.

        :param subnet: The subnet from which to allocate the address.
        :param alloc_type: What sort of IP address to allocate in the
            range of choice in IPADDRESS_TYPE.
        :param user: If providing a user, the alloc_type must be
            IPADDRESS_TYPE.USER_RESERVED. Conversely, if the alloc_type is
            IPADDRESS_TYPE.USER_RESERVED the user must also be provided.
            AssertionError is raised if these conditions are not met.
        :param requested_address: Optional IP address that the caller wishes
            to use instead of being allocated one at random.
        :param exclude_addresses: A list of addresses which MUST NOT be used.
        :param restrict_ip_to_unreserved_ranges: True if the IP has to be outside
            the reserved range. In the case of reserved ips, we allow the
            ip to be within the reserved range.

        All IP parameters can be strings or netaddr.IPAddress.
        """
        # This check for `alloc_type` is important for later on. We rely on
        # detecting IntegrityError as a sign than an IP address is already
        # taken, and so we must first eliminate all other possible causes.
        self._verify_alloc_type(alloc_type, user)

        if subnet is None:
            if requested_address:
                subnet = Subnet.objects.get_best_subnet_for_ip(
                    requested_address
                )
            else:
                raise StaticIPAddressOutOfRange(
                    "Could not find an appropriate subnet."
                )

        if requested_address is None:
            with FreeIPAddress(subnet, exclude_addresses) as free_address:
                ip = self._attempt_allocation_of_free_address(
                    free_address,
                    alloc_type,
                    user=user,
                    subnet=subnet,
                )
            return ip
        else:
            requested_address = IPAddress(requested_address)
            from maasserver.models import StaticIPAddress

            if (
                StaticIPAddress.objects.filter(ip=str(requested_address))
                .exclude(alloc_type=IPADDRESS_TYPE.DISCOVERED)
                .exists()
            ):
                raise StaticIPAddressUnavailable(
                    f"The IP address {requested_address} is already in use."
                )

            subnet.validate_static_ip(
                requested_address,
                restrict_ip_to_unreserved_ranges=restrict_ip_to_unreserved_ranges,
            )
            return self._attempt_allocation(
                requested_address, alloc_type, user=user, subnet=subnet
            )


class StaticIPAddress(CleanSave, TimestampedModel):
    class Meta:
        verbose_name = "Static IP Address"
        verbose_name_plural = "Static IP Addresses"
        unique_together = ("alloc_type", "ip")
        constraints = [
            UniqueConstraint(
                fields=["ip"],
                condition=~Q(alloc_type=IPADDRESS_TYPE.DISCOVERED),
                name="maasserver_staticipaddress_discovered_uniq",
            )
        ]

    # IP can be none when a DHCP lease has expired: in this case the entry
    # in the StaticIPAddress only materializes the connection between an
    # interface and asubnet.
    ip = GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name="IP",
    )

    alloc_type = IntegerField(
        editable=False, null=False, blank=False, default=IPADDRESS_TYPE.AUTO
    )

    # Subnet is only null for IP addresses allocate before the new networking
    # model.
    subnet = ForeignKey(
        "Subnet", editable=True, blank=True, null=True, on_delete=CASCADE
    )

    user = ForeignKey(
        User,
        default=None,
        blank=True,
        null=True,
        editable=False,
        on_delete=PROTECT,
    )

    # Used only by DISCOVERED address to set the lease_time for an active
    # lease. Time is in seconds.
    lease_time = IntegerField(
        default=0, editable=False, null=False, blank=False
    )

    # Used to mark a `StaticIPAddress` as temperary until the assignment
    # can be confirmed to be free in the subnet.
    temp_expires_on = DateTimeField(
        null=True, blank=True, editable=False, db_index=True
    )

    objects = StaticIPAddressManager()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._previous_subnet_id = None
        self._previous_temp_expires_on = None
        self._previous_ip = None
        self._updated = False

    def __setattr__(self, name, value):
        if (
            hasattr(self, f"_previous_{name}")
            and getattr(self, f"_previous_{name}") is None
        ):
            setattr(self, f"_previous_{name}", getattr(self, name))
            self._updated = True
        super().__setattr__(name, value)

    def __str__(self):
        # Attempt to show the symbolic alloc_type name if possible.
        type_names = map_enum_reverse(IPADDRESS_TYPE)
        strtype = type_names.get(self.alloc_type, "%s" % self.alloc_type)
        return f"{self.ip}:type={strtype}"

    @property
    def alloc_type_name(self):
        """Returns a human-readable representation of the `alloc_type`."""
        return IPADDRESS_TYPE_CHOICES_DICT.get(self.alloc_type, "")

    def get_node(self):
        """Return the Node of the first Interface connected to this IP
        address."""
        interface = self.get_interface()
        if interface is not None:
            return interface.get_node()
        else:
            return None

    def get_interface(self):
        """Return the first Interface connected to this IP address."""
        # Note that, while this relationship is modeled as a many-to-many,
        # MAAS currently only relates a single interface per IP address
        # at this time. In the future, we may want to model virtual IPs, in
        # which case this will need to change.
        return self.interface_set.first()

    def get_interface_link_type(self):
        """Return the `INTERFACE_LINK_TYPE`."""
        if self.alloc_type == IPADDRESS_TYPE.AUTO:
            return INTERFACE_LINK_TYPE.AUTO
        elif self.alloc_type in (
            IPADDRESS_TYPE.DHCP,
            IPADDRESS_TYPE.DISCOVERED,
        ):
            return INTERFACE_LINK_TYPE.DHCP
        elif self.alloc_type == IPADDRESS_TYPE.USER_RESERVED:
            return INTERFACE_LINK_TYPE.STATIC
        elif self.alloc_type == IPADDRESS_TYPE.STICKY:
            if not self.ip:
                return INTERFACE_LINK_TYPE.LINK_UP
            else:
                return INTERFACE_LINK_TYPE.STATIC
        else:
            raise ValueError("Unknown alloc_type.")

    def get_log_name_for_alloc_type(self):
        """Return a nice log name for the `alloc_type` of the IP address."""
        return IPADDRESS_TYPE_CHOICES_DICT[self.alloc_type]

    def is_linked_to_one_unknown_interface(self):
        """Return True if the IP address is only linked to one unknown
        interface."""
        interface_types = [
            interface.type for interface in self.interface_set.all()
        ]
        return interface_types == [INTERFACE_TYPE.UNKNOWN]

    def is_safe_to_delete(self):
        """Return True if the IP is user reserved and not associated with any interface."""
        return (
            self.alloc_type == IPADDRESS_TYPE.USER_RESERVED
            and self.interface_set.count() == 0
        )

    def get_ip(self):
        """Return the IP address assigned."""
        ip, subnet = self.get_ip_and_subnet()
        return ip

    def get_ip_and_subnet(self):
        """Return the IP address and subnet assigned.

        For all alloc_types except DHCP it returns `ip` and `subnet`. When
        `alloc_type` is DHCP it returns the associated DISCOVERED `ip` and
        `subnet` on the same linked interfaces.
        """
        if self.alloc_type == IPADDRESS_TYPE.DHCP:
            discovered_ip = self._get_related_discovered_ip()
            if discovered_ip is not None:
                return discovered_ip.ip, discovered_ip.subnet
        return self.ip, self.subnet

    def clean_subnet_and_ip_consistent(self):
        """Validate that the IP address is inside the subnet."""

        # USER_RESERVED addresses must have an IP address specified.
        # Blank AUTO, STICKY and DHCP addresses have a special meaning:
        # - Blank AUTO addresses mean the interface will get an IP address
        #   auto assigned when it goes to be deployed.
        # - Blank STICKY addresses mean the interface should come up and be
        #   associated with a particular Subnet, but no IP address should
        #   be assigned.
        # - DHCP IP addresses are always blank. The model will look for
        #   a DISCOVERED IP address on the same interface to map to the DHCP
        #   IP address with `get_ip()`.
        if self.alloc_type == IPADDRESS_TYPE.USER_RESERVED:
            if not self.ip:
                raise ValidationError(
                    {"ip": ["IP address must be specified."]}
                )
        if self.alloc_type == IPADDRESS_TYPE.DHCP:
            if self.ip:
                raise ValidationError(
                    {"ip": ["IP address must not be specified."]}
                )

        if self.ip and self.subnet and self.subnet.cidr:
            address = self.get_ipaddress()
            network = self.subnet.get_ipnetwork()
            if address not in network:
                raise ValidationError(
                    {
                        "ip": [
                            f"IP address {address} is not within the subnet: {network}."
                        ]
                    }
                )

    def get_ipaddress(self):
        """Returns this StaticIPAddress wrapped in an IPAddress object.

        :return: An IPAddress, (or None, if the IP address is unspecified)
        """
        if self.ip:
            return IPAddress(self.ip)
        else:
            return None

    def get_mac_addresses(self):
        """Return set of all MAC's linked to this ip."""
        return {
            interface.mac_address for interface in self.interface_set.all()
        }

    def clean(self, *args, **kwargs):
        super().clean(*args, **kwargs)
        self.clean_subnet_and_ip_consistent()

    def _set_subnet(self, subnet, interfaces=None):
        """Resets the Subnet for this StaticIPAddress, making sure to update
        the VLAN for a related Interface (if the VLAN has changed).
        """
        self.subnet = subnet
        if interfaces is not None:
            for iface in interfaces:
                if (
                    iface is not None
                    and subnet is not None
                    and iface.vlan_id != subnet.vlan_id
                ):
                    iface.vlan = subnet.vlan
                    iface.save()

    def render_json(
        self, with_username: bool = False, with_summary: bool = False
    ) -> dict:
        """Render a representation of this `StaticIPAddress` object suitable
        for converting to JSON. Includes optional parameters wherever a join
        would be implied by including a specific piece of information."""
        # XXX mpontillo 2016-03-11 we should do the formatting client side.
        from maasserver.websockets.base import dehydrate_datetime

        data = {
            "ip": self.ip,
            "alloc_type": self.alloc_type,
            "created": dehydrate_datetime(self.created),
            "updated": dehydrate_datetime(self.updated),
        }
        if with_username and self.user is not None:
            data["user"] = self.user.username
        if with_summary:
            iface = self.get_interface()
            node = self.get_node()
            if node is not None:
                data["node_summary"] = {
                    "system_id": node.system_id,
                    "node_type": node.node_type,
                    "fqdn": node.fqdn,
                    "hostname": node.hostname,
                    "is_container": node.parent_id is not None,
                }
                if iface is not None:
                    data["node_summary"]["via"] = iface.name
                if (
                    with_username
                    and self.alloc_type != IPADDRESS_TYPE.DISCOVERED
                ):
                    # If a user owns this node, overwrite any username we found
                    # earlier. A node's owner takes precedence.
                    if node.owner and node.owner.username:
                        data["user"] = node.owner.username
            # This IP address is used as DNS resource.
            dns_records = [
                {
                    "id": resource.id,
                    "name": resource.name,
                    "domain": resource.domain.name,
                }
                for resource in self.dnsresource_set.all()
            ]
            if dns_records:
                data["dns_records"] = dns_records
            # This IP address is used as a BMC.
            bmcs = [
                {
                    "id": bmc.id,
                    "power_type": bmc.power_type,
                    "nodes": [
                        {
                            "system_id": node.system_id,
                            "hostname": node.hostname,
                        }
                        for node in bmc.node_set.all()
                    ],
                }
                for bmc in self.bmc_set.all()
            ]
            if bmcs:
                data["bmcs"] = bmcs
        return data

    def set_ip_address(self, ipaddr):
        """Sets the IP address to the specified value, and also updates
        the subnet field.

        The new subnet is determined by calling get_best_subnet_for_ip() on
        the SubnetManager.

        If an interface is supplied, the Interface's VLAN is also updated
        to match the VLAN of the new Subnet.
        """
        self.ip = ipaddr

        # Cases we need to handle:
        # (0) IP address is being cleared out (remains within Subnet)
        # (1) IP address changes to another address within the same Subnet
        # (2) IP address changes to another address with a different Subnet
        # (3) IP address changes to an address within an unknown Subnet

        if not ipaddr:
            # (0) Nothing to be done. We're clearing out the IP address.
            return

        if self.ip and self.subnet:
            if self.get_ipaddress() in self.subnet.get_ipnetwork():
                # (1) Nothing to be done. Already in an appropriate Subnet.
                return
            else:
                # (2) and (3): the Subnet has changed (could be to None)
                subnet = Subnet.objects.get_best_subnet_for_ip(ipaddr)
                # We must save here, otherwise it's possible that we can't
                # traverse the interface_set many-to-many.
                self.save()
                self._set_subnet(subnet, interfaces=self.interface_set.all())

    def _get_related_discovered_ip(self):
        """Return the related DISCOVERED IP address for this IP address.

        This comes from looking at the DISCOVERED IP addresses assigned to the
        related interfaces.
        """
        return (
            StaticIPAddress.objects.filter(
                interface__in=self.interface_set.all(),
                alloc_type=IPADDRESS_TYPE.DISCOVERED,
                ip__isnull=False,
            )
            .order_by("-id")
            .first()
        )

    def save(self, *args, **kwargs):
        configure_dhcp = self.alloc_type != IPADDRESS_TYPE.DISCOVERED and (
            (self.id is None and self.ip)
            or (
                self.id is not None
                and self._updated
                and (
                    self._previous_ip != self.ip
                    or self._previous_temp_expires_on != self.temp_expires_on
                    or self._previous_subnet_id != self.subnet_id
                )
            )
        )

        super().save(*args, **kwargs)

        if configure_dhcp:
            params = (
                ConfigureDHCPParam(
                    subnet_ids=[self._previous_subnet_id, self.subnet_id]
                )
                if self._previous_subnet_id
                else ConfigureDHCPParam(static_ip_addr_ids=[self.id])
            )

            post_commit_do(
                start_workflow,
                workflow_name=CONFIGURE_DHCP_WORKFLOW_NAME,
                param=params,
                task_queue="region",
            )

    def delete(self, *args, **kwargs):
        subnet_id = self.subnet_id
        alloc_type = self.alloc_type
        temp_expires = self.temp_expires_on

        super().delete(*args, **kwargs)

        if (
            alloc_type != IPADDRESS_TYPE.DISCOVERED
            and temp_expires is not None
        ):
            post_commit_do(
                start_workflow,
                workflow_name=CONFIGURE_DHCP_WORKFLOW_NAME,
                param=ConfigureDHCPParam(subnet_ids=[subnet_id]),
                task_queue="region",
            )
