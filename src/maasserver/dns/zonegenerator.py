# Copyright 2014-2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""DNS zone generator."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from itertools import chain

import attr
from netaddr import IPAddress, IPNetwork

from maascommon.dns import HostnameIPMapping, HostnameRRsetMapping
from maasserver import logger
from maasserver.enum import IPRANGE_TYPE, RDNS_MODE
from maasserver.exceptions import UnresolvableHost
from maasserver.models.config import Config
from maasserver.models.dnsresource import separate_fqdn
from maasserver.models.domain import Domain
from maasserver.models.iprange import IPRange
from maasserver.models.subnet import Subnet
from maasserver.server_address import get_maas_facing_server_addresses
from maasserver.sqlalchemy import service_layer
from provisioningserver.dns.config import DynamicDNSUpdate
from provisioningserver.dns.zoneconfig import (
    DNSForwardZoneConfig,
    DNSReverseZoneConfig,
)


class lazydict(dict):
    """A `dict` that lazily populates itself.

    Somewhat like a :class:`collections.defaultdict`, but that the factory
    function is called with the missing key, and the value returned is saved.
    """

    __slots__ = ("factory",)

    def __init__(self, factory):
        super().__init__()
        self.factory = factory

    def __missing__(self, key):
        value = self[key] = self.factory(key)
        return value


def sequence(thing):
    """Make a sequence from `thing`.

    If `thing` is a sequence, return it unaltered. If it's iterable, return a
    list of its elements. Otherwise, return `thing` as the sole element in a
    new list.
    """
    if isinstance(thing, Sequence):
        return thing
    elif isinstance(thing, Iterable):
        return list(thing)
    else:
        return [thing]


def get_hostname_ip_mapping(
    domain_id: int | None = None,
) -> dict[str, HostnameIPMapping]:
    """Return a mapping {hostnames -> info} for the allocated nodes in
    `domain` or `subnet`.  Info contains: ttl, ips, system_id.
    """
    return service_layer.services.domains.get_hostname_ip_mapping(domain_id)


def get_hostname_dnsdata_mapping(domain_id: int):
    """Return a mapping {hostnames -> info} for the allocated nodes in
    `domain`.  Info contains: system_id and rrsets (which contain (ttl, rrtype,
    rrdata) tuples.
    """
    return service_layer.services.domains.get_hostname_dnsdata_mapping(
        domain_id, with_ids=False
    )


WARNING_MESSAGE = (
    "The DNS server will use the address '%s',  which is inside the "
    "loopback network.  This may not be a problem if you're not using "
    "MAAS's DNS features or if you don't rely on this information. "
    "Consult the command: 'maas-region local_config_set --maas-url'"
    "(deb installs) or 'maas config --maas-url' (snap installs)"
    "for details on how to set the MAAS URL."
)


def warn_loopback(ip):
    """Warn if the given IP address is in the loopback network."""
    if IPAddress(ip).is_loopback():
        logger.warning(WARNING_MESSAGE % ip)


def get_dns_server_address(rack_controller=None, ipv4=True, ipv6=True):
    """Return a single DNS server IP address (based on address family).

    That address is derived from the config maas_url or rack_controller.url.
    Consult the command: 'maas-region local_config_set --maas-url' (deb
    installs) or 'maas config --maas-url' (snap installs) for details on how
    to set the MAAS URL.

    :param rack_controller: Optional rack controller to which the DNS server
        should be accessible.  If given, the server address will be taken from
        the rack controller's `maas_url` setting.  Otherwise, it will be taken
        from the globally configured default MAAS URL.
    :param ipv4: Include IPv4 server addresses?
    :param ipv6: Include IPv6 server addresses?

    """
    iplist = get_dns_server_addresses(rack_controller, ipv4, ipv6)
    if iplist:
        return min(iplist).format()
    else:
        return None


def get_dns_server_addresses(
    rack_controller=None,
    ipv4=True,
    ipv6=True,
    include_alternates=False,
    default_region_ip=None,
    filter_allowed_dns=True,
):
    """Return the DNS server's IP addresses.

    That address is derived from the config maas_url or rack_controller.url.
    Consult the command: 'maas-region local_config_set --maas-url' (deb
    installs) or 'maas config --maas-url' (snap installs) for details on how
    to set the MAAS URL.

    :param rack_controller: Optional rack controller to which the DNS server
        should be accessible.  If given, the server addresses will be taken
        from the rack controller's `maas_url` setting.  Otherwise, it will be
        taken from the globally configured default MAAS URL.
    :param ipv4: Include IPv4 server addresses?
    :param ipv6: Include IPv6 server addresses?
    :param include_alternates: Include IP addresses from other regions?
    :param default_region_ip: The default source IP address to be used, if a
        specific URL is not defined.
    :param filter_allowed_dns: If true, only include addresses for subnets
        with allow_dns=True.
    :return: List of IPAddress to use.  Loopback addresses are removed from the
        list, unless there are no non-loopback addresses.

    """
    try:
        ips = get_maas_facing_server_addresses(
            rack_controller=rack_controller,
            ipv4=ipv4,
            ipv6=ipv6,
            include_alternates=include_alternates,
            default_region_ip=default_region_ip,
        )
    except OSError as e:
        raise UnresolvableHost(  # noqa: B904
            "Unable to find MAAS server IP address: %s. MAAS's DNS server "
            "requires this IP address for the NS records in its zone files. "
            "Make sure that the configuration setting for the MAAS URL has "
            "the correct hostname. Consult the command 'maas-region "
            "local_config_set --maas-url' (deb installs) or 'maas config "
            "--maas-url' (snap installs) for more details." % e.strerror
        )

    if filter_allowed_dns:
        ips = [
            ip
            for ip in ips
            if getattr(
                Subnet.objects.get_best_subnet_for_ip(ip), "allow_dns", True
            )
        ]
    non_loop = [ip for ip in ips if not ip.is_loopback()]
    if non_loop:
        return non_loop
    else:
        for ip in ips:
            warn_loopback(ip)
        return ips


def get_dns_search_paths():
    """Return all the search paths for the DNS server."""
    return {
        name
        for name in Domain.objects.filter(authoritative=True).values_list(
            "name", flat=True
        )
        if name
    }


class ZoneGenerator:
    """Generate zones describing those relating to the given domains and
    subnets.

    We generate zones for the domains (forward), and subnets (reverse) passed.
    """

    def __init__(
        self,
        domains,
        subnets,
        default_ttl=None,
        serial=None,
        internal_domains=None,
        dynamic_updates=None,
        force_config_write=False,
    ):
        """
        :param serial: A serial number to reuse when creating zones in bulk.
        """
        self.domains = sequence(domains)
        self.subnets = sequence(subnets)
        if default_ttl is None:
            self.default_ttl = Config.objects.get_config("default_dns_ttl")
        else:
            self.default_ttl = default_ttl
        self.default_domain = Domain.objects.get_default_domain()
        self.serial = serial
        self.internal_domains = internal_domains
        if self.internal_domains is None:
            self.internal_domains = []
        self._dynamic_updates = dynamic_updates
        if self._dynamic_updates is None:
            self._dynamic_updates = []
        self.force_config_write = force_config_write  # some data changed that nsupdate cannot update if true
        self._existing_subnet_cfgs = {}

    @staticmethod
    def _get_mappings():
        """Return a lazily evaluated mapping dict."""
        return lazydict(get_hostname_ip_mapping)

    @staticmethod
    def _get_rrset_mappings():
        """Return a lazily evaluated mapping dict."""
        return lazydict(get_hostname_dnsdata_mapping)

    @staticmethod
    def _gen_forward_zones(
        domains,
        serial,
        ns_host_name,
        mappings,
        rrset_mappings,
        default_ttl,
        internal_domains,
        dynamic_updates,
        force_config_write,
    ):
        """Generator of forward zones, collated by domain name."""
        dns_ip_list = get_dns_server_addresses(filter_allowed_dns=False)
        domains = set(domains)

        # For each of the domains that we are generating, create the zone from:
        # 1. Node: ip mapping(domain) (which includes dnsresource addresses).
        # 2. Dnsresource non-address records in this domain.
        # 3. For the default domain all forward look ups for the managed and
        #    unmanaged dynamic ranges.
        for domain in domains:
            zone_ttl = default_ttl if domain.ttl is None else domain.ttl
            # 1. node: ip mapping(domain)
            # Map all of the nodes in this domain, including the user-reserved
            # ip addresses.  Separate_fqdn handles top-of-domain names needing
            # to have the name '@', and we already know the domain name, so we
            # discard that part of the return.
            mapping = {
                separate_fqdn(hostname, domainname=domain.name)[0]: info
                for hostname, info in mappings[domain.id].items()
            }
            # 2a. Create non-address records.  Specifically ignore any CNAME
            # records that collide with addresses in mapping.
            other_mapping = rrset_mappings[domain.id]

            # 2b. Capture NS RRsets for anything that is a child of this domain
            domain.add_delegations(
                other_mapping, ns_host_name, dns_ip_list, default_ttl
            )

            # 3. All of the special handling for the default domain.
            dynamic_ranges = []
            if domain.is_default():
                # 3a. All forward entries for the managed and unmanaged dynamic
                # ranges go into the default domain.
                subnets = Subnet.objects.all().prefetch_related("iprange_set")
                for subnet in subnets:
                    # We loop through the whole set so the prefetch above works
                    # in one query.
                    for ip_range in subnet.iprange_set.all():
                        if ip_range.type == IPRANGE_TYPE.DYNAMIC:
                            dynamic_ranges.append(ip_range.get_MAASIPRange())
                # 3b. Add A/AAAA RRset for @.  If glue is needed for any other
                # domain, adding the glue is the responsibility of the admin.
                ttl = domain.get_base_ttl("A", default_ttl)
                for dns_ip in dns_ip_list:
                    if dns_ip.version == 4:
                        other_mapping["@"].rrset.add(
                            (ttl, "A", dns_ip.format())
                        )
                    else:
                        other_mapping["@"].rrset.add(
                            (ttl, "AAAA", dns_ip.format())
                        )

            domain_updates = [
                update
                for update in dynamic_updates
                if update.zone == domain.name
            ]

            yield DNSForwardZoneConfig(
                domain.name,
                serial=serial,
                default_ttl=zone_ttl,
                ns_ttl=domain.get_base_ttl("NS", default_ttl),
                ipv4_ttl=domain.get_base_ttl("A", default_ttl),
                ipv6_ttl=domain.get_base_ttl("AAAA", default_ttl),
                mapping=mapping,
                ns_host_name=ns_host_name,
                other_mapping=other_mapping,
                dynamic_ranges=dynamic_ranges,
                dynamic_updates=domain_updates,
                force_config_write=force_config_write,
            )

        # Create the forward zone config for the internal domains.
        for internal_domain in internal_domains:
            # Use other_mapping to create the domain resources.
            other_mapping = defaultdict(HostnameRRsetMapping)
            for resource in internal_domain.resources:
                resource_mapping = other_mapping[resource.name]
                for record in resource.records:
                    resource_mapping.rrset.add(
                        (internal_domain.ttl, record.rrtype, record.rrdata)
                    )

            domain_updates = [
                update
                for update in dynamic_updates
                if update.zone == internal_domain.name
            ]

            yield DNSForwardZoneConfig(
                internal_domain.name,
                serial=serial,
                default_ttl=internal_domain.ttl,
                ns_ttl=internal_domain.ttl,
                ipv4_ttl=internal_domain.ttl,
                ipv6_ttl=internal_domain.ttl,
                mapping={},
                ns_host_name=ns_host_name,
                other_mapping=other_mapping,
                dynamic_ranges=[],
                dynamic_updates=domain_updates,
                force_config_write=force_config_write,
            )

    @staticmethod
    def _split_large_subnet(network: IPNetwork) -> list[IPNetwork]:
        # Generate the name of the reverse zone file:
        # Use netaddr's reverse_dns() to get the reverse IP name
        # of the first IP address in the network and then drop the first
        # octets of that name (i.e. drop the octets that will be specified in
        # the zone file).
        # returns a list of (IPNetwork, zone_name, zonefile_path) tuples
        new_networks = []
        first = IPAddress(network.first)
        last = IPAddress(network.last)
        if first.version == 6:
            # IPv6.
            # 2001:89ab::/19 yields 8.1.0.0.2.ip6.arpa, and the full list
            # is 8.1.0.0.2.ip6.arpa, 9.1.0.0.2.ip6.arpa
            # The ipv6 reverse dns form is 32 elements of 1 hex digit each.
            # How many elements of the reverse DNS name to we throw away?
            # Prefixlen of 0-3 gives us 1, 4-7 gives us 2, etc.
            # While this seems wrong, we always _add_ a base label back in,
            # so it's correct.
            rest_limit = (132 - network.prefixlen) // 4
            # What is the prefix for each inner subnet (It will be the next
            # smaller multiple of 4.)  If it's the smallest one, then RFC2317
            # tells us that we're adding an extra blob to the front of the
            # reverse zone name, and we want the entire prefixlen.
            subnet_prefix = (network.prefixlen + 3) // 4 * 4
            if subnet_prefix == 128:
                subnet_prefix = network.prefixlen
            # How big is the step between subnets?  Again, special case for
            # extra small subnets.
            step = 1 << ((128 - network.prefixlen) // 4 * 4)
            if step < 16:
                step = 16
            # Grab the base (hex) and trailing labels for our reverse zone.
            split_zone = first.reverse_dns.split(".")
            base = int(split_zone[rest_limit - 1], 16)
        else:
            # IPv4.
            # The logic here is the same as for IPv6, but with 8 instead of 4.
            rest_limit = (40 - network.prefixlen) // 8
            subnet_prefix = (network.prefixlen + 7) // 8 * 8
            if subnet_prefix == 32:
                subnet_prefix = network.prefixlen
            step = 1 << ((32 - network.prefixlen) // 8 * 8)
            if step < 256:
                step = 256
            # Grab the base (decimal) and trailing labels for our reverse
            # zone.
            split_zone = first.reverse_dns.split(".")
            base = int(split_zone[rest_limit - 1])

        while first <= last:
            if first > last:
                # if the excluding subnet pushes the base IP beyond the bounds of the generating subnet, we've reached the end and return early
                return new_networks

            new_networks.append(IPNetwork(f"{first}/{subnet_prefix:d}"))
            base += 1
            try:
                first += step
            except IndexError:
                # IndexError occurs when we go from 255.255.255.255 to
                # 0.0.0.0.  If we hit that, we're all fine and done.
                break
        return new_networks

    @staticmethod
    def _filter_mapping_for_network(
        network: IPNetwork, mappings: dict[str, HostnameIPMapping]
    ):
        net_mappings = {}
        for k, v in mappings.items():
            if ips_in_net := set(
                ip for ip in v.ips if IPAddress(str(ip)) in network
            ):
                net_mappings[k] = HostnameIPMapping(
                    v.system_id,
                    v.ttl,
                    ips_in_net,
                    v.node_type,
                    v.dnsresource_id,
                    v.user_id,
                )

        return net_mappings

    @staticmethod
    def _generate_glue_nets(subnets: list[Subnet]):
        # Generate the list of parent networks for rfc2317 glue.  Note that we
        # need to handle the case where we are controlling both the small net
        # and a bigger network containing the /24, not just a /24 network.
        rfc2317_glue = {}
        for subnet in subnets:
            network = IPNetwork(subnet.cidr)
            if subnet.rdns_mode == RDNS_MODE.RFC2317:
                # If this is a small subnet and  we are doing RFC2317 glue for
                # it, then we need to combine that with any other such subnets
                # We need to know this before we start creating reverse DNS
                # zones.
                if network.version == 4 and network.prefixlen > 24:
                    # Turn 192.168.99.32/29 into 192.168.99.0/24
                    basenet = IPNetwork(
                        "%s/24" % IPNetwork("%s/24" % network.network).network
                    )
                    rfc2317_glue.setdefault(basenet, set()).add(network)
                elif network.version == 6 and network.prefixlen > 124:
                    basenet = IPNetwork(
                        "%s/124"
                        % IPNetwork("%s/124" % network.network).network
                    )
                    rfc2317_glue.setdefault(basenet, set()).add(network)

        return rfc2317_glue

    @staticmethod
    def _find_glue_nets(
        network: IPNetwork, rfc2317_glue: defaultdict[str, set[IPNetwork]]
    ):
        # Use the default_domain as the name for the NS host in the reverse
        # zones.  If this network is actually a parent rfc2317 glue
        # network, then we need to generate the glue records.
        # We need to detect the need for glue in our networks that are
        # big.
        if (
            network.version == 6 and network.prefixlen < 124
        ) or network.prefixlen < 24:
            glue = set()
            # This is the reason for needing the subnets sorted in
            # increasing order of size.
            for net in rfc2317_glue.copy().keys():
                if net in network:
                    glue.update(rfc2317_glue[net])
                    del rfc2317_glue[net]
        elif network in rfc2317_glue:
            glue = rfc2317_glue[network]
            del rfc2317_glue[network]
        else:
            glue = set()
        return glue

    @staticmethod
    def _merge_into_existing_network(
        network: IPNetwork,
        existing: dict[IPNetwork, DNSReverseZoneConfig],
        mapping: dict[str, HostnameIPMapping],
        dynamic_ranges: list[IPRange] | None = None,
        dynamic_updates: list[DynamicDNSUpdate] | None = None,
        glue: set[IPNetwork] | None = None,
        is_glue_net: bool = False,
    ):
        if dynamic_ranges is None:
            dynamic_ranges = []
        if dynamic_updates is None:
            dynamic_updates = []
        if glue is None:
            glue = set()
        # since all dynamic updates are passed and we then filter for those belonging
        # in the network, the existing config already has all updates and we do not need
        # to merge them, just add them if they haven't already
        if not existing[network]._dynamic_updates:
            existing[network]._dynamic_updates = dynamic_updates
        existing[network]._rfc2317_ranges = existing[
            network
        ]._rfc2317_ranges.union(glue)
        for k, v in mapping.items():
            if k in existing[network]._mapping:
                existing[network]._mapping[k].ips.union(v.ips)
            else:
                existing[network]._mapping[k] = v
        existing[network]._dynamic_ranges += dynamic_ranges
        for glue_net in glue.union(existing[network]._rfc2317_ranges):
            for k, v in existing[network]._mapping.copy().items():
                if ip_set := set(
                    ip for ip in v.ips if IPAddress(str(ip)) not in glue_net
                ):
                    existing[network]._mapping[k].ips = ip_set
                else:
                    del existing[network]._mapping[k]

    @staticmethod
    def _gen_reverse_zones(
        subnets,
        serial,
        ns_host_name,
        mappings,
        default_ttl,
        dynamic_updates,
        force_config_write,
        existing_subnet_cfgs=None,
    ):
        """Generator of reverse zones, sorted by network."""

        if existing_subnet_cfgs is None:
            existing_subnet_cfgs = {}

        subnets = set(subnets)

        rfc2317_glue = ZoneGenerator._generate_glue_nets(subnets)

        # get_hostname_ip_mapping expects a domain_id or None, just pass None
        # if the mapping is not related to a domain.
        if len(subnets):
            mappings["reverse"] = mappings[None]

        # For each of the zones that we are generating (one or more per
        # subnet), compile the zone from:
        # 1. Dynamic ranges on this subnet.
        # 2. Node: ip mapping(subnet), including DNSResource records for
        #    StaticIPAddresses in this subnet.
        # All of this needs to be done smallest to largest so that we can
        # correctly gather the rfc2317 glue that we need.  Failure to sort
        # means that we wind up grabbing (and deleting) the rfc2317 glue info
        # while processing the wrong network.
        for subnet in sorted(
            subnets,
            key=lambda subnet: IPNetwork(subnet.cidr).prefixlen,
            reverse=True,
        ):
            base_network = IPNetwork(subnet.cidr)
            if subnet.rdns_mode == RDNS_MODE.DISABLED:
                # If we are not doing reverse dns for this subnet, then just
                # skip to the next subnet.
                logger.debug(
                    "%s disabled subnet in DNS config list" % subnet.cidr
                )
                continue

            networks = ZoneGenerator._split_large_subnet(base_network)

            # 1. Figure out the dynamic ranges.
            dynamic_ranges = [
                ip_range.netaddr_iprange
                for ip_range in subnet.get_dynamic_ranges()
            ]

            for network in networks:
                # 2. Start with the map of all of the nodes, including all
                # DNSResource-associated addresses.  We will prune this to just
                # entries for the subnet when we actually generate the zonefile.
                # If we get here, then we have subnets, so we noticed that above
                # and created mappings['reverse'].  LP#1600259
                mapping = ZoneGenerator._filter_mapping_for_network(
                    network, mappings["reverse"]
                )

                glue = ZoneGenerator._find_glue_nets(network, rfc2317_glue)
                domain_updates = [
                    DynamicDNSUpdate.as_reverse_record_update(update, network)
                    for update in dynamic_updates
                    if update.answer
                    and update.answer_is_ip
                    and (update.answer_as_ip in network)
                ]

                if network in existing_subnet_cfgs:
                    ZoneGenerator._merge_into_existing_network(
                        network,
                        existing_subnet_cfgs,
                        mapping,
                        dynamic_ranges=dynamic_ranges,
                        dynamic_updates=domain_updates,
                        glue=glue,
                    )
                else:
                    existing_subnet_cfgs[network] = DNSReverseZoneConfig(
                        ns_host_name,
                        serial=serial,
                        default_ttl=default_ttl,
                        ns_host_name=ns_host_name,
                        mapping=mapping,
                        network=network,
                        dynamic_ranges=dynamic_ranges,
                        rfc2317_ranges=glue,
                        dynamic_updates=domain_updates,
                        force_config_write=force_config_write,
                    )

                    yield existing_subnet_cfgs[network]

            # Now provide any remaining rfc2317 glue networks.
            for network, ranges in rfc2317_glue.items():
                exclude_set = {
                    IPNetwork(s.cidr)
                    for s in subnets
                    if network in IPNetwork(s.cidr)
                }
                domain_updates = []
                for update in dynamic_updates:
                    glue_update = True
                    for exclude_net in exclude_set:
                        if (
                            update.answer
                            and update.answer_is_ip
                            and update.answer_as_ip in exclude_net
                        ):
                            glue_update = False
                            break
                    if (
                        glue_update
                        and update.answer
                        and update.answer_is_ip
                        and update.answer_as_ip in network
                    ):
                        domain_updates.append(
                            DynamicDNSUpdate.as_reverse_record_update(
                                update, network
                            )
                        )

                if network in existing_subnet_cfgs:
                    ZoneGenerator._merge_into_existing_network(
                        network,
                        existing_subnet_cfgs,
                        mapping,
                        dynamic_updates=domain_updates,
                        glue=ranges,
                        is_glue_net=True,
                    )
                else:
                    existing_subnet_cfgs[network] = DNSReverseZoneConfig(
                        ns_host_name,
                        serial=serial,
                        default_ttl=default_ttl,
                        network=network,
                        ns_host_name=ns_host_name,
                        rfc2317_ranges=ranges,
                        dynamic_updates=domain_updates,
                        force_config_write=force_config_write,
                    )
                    yield existing_subnet_cfgs[network]

    def __iter__(self):
        """Iterate over zone configs.

        Yields `DNSForwardZoneConfig` and `DNSReverseZoneConfig` configs.
        """
        # For testing and such it's fine if we don't have a serial, but once
        # we get to this point, we really need one.
        assert self.serial is not None, "No serial number specified."

        mappings = self._get_mappings()
        ns_host_name = self.default_domain.name
        rrset_mappings = self._get_rrset_mappings()
        serial = self.serial
        default_ttl = self.default_ttl
        return chain(
            self._gen_forward_zones(
                self.domains,
                serial,
                ns_host_name,
                mappings,
                rrset_mappings,
                default_ttl,
                self.internal_domains,
                self._dynamic_updates,
                self.force_config_write,
            ),
            self._gen_reverse_zones(
                self.subnets,
                serial,
                ns_host_name,
                mappings,
                default_ttl,
                self._dynamic_updates,
                self.force_config_write,
                existing_subnet_cfgs=self._existing_subnet_cfgs,
            ),
        )

    def as_list(self):
        """Return the zones as a list."""
        return list(self)


@attr.s
class InternalDomain:
    """Configuration for the internal domain."""

    # Name of the domain.
    name = attr.ib(converter=str)

    # TTL for the domain.
    ttl = attr.ib(converter=int)

    # Resources for this domain.
    resources = attr.ib(converter=list)


@attr.s
class InternalDomainResourse:
    """Resource inside the internal domain."""

    # Name of the resource.
    name = attr.ib(converter=str)

    # Records for this resource.
    records = attr.ib(converter=list)


@attr.s
class InternalDomainResourseRecord:
    """Record inside an internal domain resource."""

    # Type of the resource record.
    rrtype = attr.ib(converter=str)

    # Data inside resource record.
    rrdata = attr.ib(converter=str)
