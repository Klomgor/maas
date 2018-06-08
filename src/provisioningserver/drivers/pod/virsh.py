# Copyright 2017-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Virsh pod driver."""

__all__ = [
    'probe_virsh_and_enlist',
    'VirshPodDriver',
    ]

import string
from tempfile import NamedTemporaryFile
from textwrap import dedent
from uuid import uuid4

from lxml import etree
import pexpect
from provisioningserver.drivers import (
    IP_EXTRACTOR_PATTERNS,
    make_ip_extractor,
    make_setting_field,
    SETTING_SCOPE,
)
from provisioningserver.drivers.pod import (
    Capabilities,
    DiscoveredMachine,
    DiscoveredMachineBlockDevice,
    DiscoveredMachineInterface,
    DiscoveredPod,
    DiscoveredPodHints,
    DiscoveredPodStoragePool,
    PodDriver,
)
from provisioningserver.logger import get_maas_logger
from provisioningserver.rpc.exceptions import PodInvalidResources
from provisioningserver.rpc.utils import (
    commission_node,
    create_node,
)
from provisioningserver.utils import (
    shell,
    typed,
)
from provisioningserver.utils.network import generate_mac_address
from provisioningserver.utils.shell import get_env_with_locale
from provisioningserver.utils.twisted import (
    asynchronous,
    synchronous,
)
from twisted.internet.defer import inlineCallbacks
from twisted.internet.threads import deferToThread


maaslog = get_maas_logger("drivers.pod.virsh")


XPATH_ARCH = "/domain/os/type/@arch"
XPATH_BOOT = "/domain/os/boot"
XPATH_OS = "/domain/os"

XPATH_POOL_TYPE = "/pool/@type"
XPATH_POOL_AVAILABLE = "/pool/available"
XPATH_POOL_CAPACITY = "/pool/capacity"
XPATH_POOL_PATH = "/pool/target/path"
XPATH_POOL_UUID = "/pool/uuid"


DOM_TEMPLATE_AMD64 = dedent("""\
    <domain type='{type}'>
      <name>{name}</name>
      <uuid>{uuid}</uuid>
      <memory unit='MiB'>{memory}</memory>
      <vcpu>{cores}</vcpu>
      <os>
        <type arch="{arch}">hvm</type>
      </os>
      <features>
        <acpi/>
        <apic/>
      </features>
      <clock offset="utc"/>
      <on_poweroff>destroy</on_poweroff>
      <on_reboot>restart</on_reboot>
      <on_crash>restart</on_crash>
      <pm>
        <suspend-to-mem enabled='no'/>
        <suspend-to-disk enabled='no'/>
      </pm>
      <devices>
        <emulator>{emulator}</emulator>
        <controller type='pci' index='0' model='pci-root'/>
        <controller type='virtio-serial' index='0'>
          <address type='pci' domain='0x0000'
            bus='0x00' slot='0x05' function='0x0'/>
        </controller>
        <serial type='pty'>
          <target port='0'/>
        </serial>
        <console type='pty'>
          <target type='serial' port='0'/>
        </console>
        <channel type='spicevmc'>
          <target type='virtio' name='com.redhat.spice.0'/>
          <address type='virtio-serial' controller='0' bus='0' port='1'/>
        </channel>
        <graphics type='spice' autoport='yes'>
          <image compression='off'/>
        </graphics>
        <input type='mouse' bus='ps2'/>
        <input type='keyboard' bus='ps2'/>
      </devices>
    </domain>
    """)

DOM_TEMPLATE_ARM64 = dedent("""\
    <domain type='{type}'>
      <name>{name}</name>
      <uuid>{uuid}</uuid>
      <memory unit='MiB'>{memory}</memory>
      <vcpu>{cores}</vcpu>
      <cpu mode='host-passthrough'/>
      <os>
        <type arch='{arch}' machine='virt'>hvm</type>
        <loader readonly='yes' type='pflash'>{loader}</loader>
        <nvram template='{loader}'>{nvram_path}/{name}_VARS.fd</nvram>
      </os>
      <features>
        <acpi/>
        <apic/>
        <pae/>
        <gic version='3'/>
      </features>
      <clock offset="utc"/>
      <on_poweroff>destroy</on_poweroff>
      <on_reboot>restart</on_reboot>
      <on_crash>restart</on_crash>
      <devices>
        <emulator>{emulator}</emulator>
        <controller type='pci' index='0' model='pcie-root'/>
        <serial type='pty'>
          <target port='0'/>
        </serial>
        <console type='pty'>
          <target type='serial' port='0'/>
        </console>
        <input type='mouse' bus='ps2'/>
        <input type='keyboard' bus='ps2'/>
      </devices>
    </domain>
    """)


DOM_TEMPLATE_PPC64 = dedent("""\
    <domain type='{type}'>
      <name>{name}</name>
      <uuid>{uuid}</uuid>
      <memory unit='MiB'>{memory}</memory>
      <vcpu>{cores}</vcpu>
      <cpu mode='host-passthrough'/>
      <os>
        <type arch='{arch}'>hvm</type>
      </os>
      <clock offset="utc"/>
      <on_poweroff>destroy</on_poweroff>
      <on_reboot>restart</on_reboot>
      <on_crash>restart</on_crash>
      <devices>
        <emulator>{emulator}</emulator>
        <controller type='pci' index='0' model='pci-root'/>
        <serial type='pty'>
          <target port='0'/>
        </serial>
        <console type='pty'>
          <target type='serial' port='0'/>
        </console>
        <input type='mouse' bus='ps2'/>
        <input type='keyboard' bus='ps2'/>
      </devices>
    </domain>
    """)

DOM_TEMPLATE_S390X = dedent("""
    <domain type='{type}'>
      <name>{name}</name>
      <uuid>{uuid}</uuid>
      <memory unit='MiB'>{memory}</memory>
      <vcpu>{cores}</vcpu>
      <os>
        <type arch="{arch}">hvm</type>
      </os>
      <features>
        <acpi/>
        <apic/>
        <pae/>
      </features>
      <devices>
        <console type='pty' tty='/dev/pts/3'>
          <source path='/dev/pts/3'/>
          <target type='sclp' port='0'/>
          <alias name='console0'/>
        </console>
      </devices>
    </domain>
    """)


# Virsh stores the architecture with a different
# label then MAAS. This maps virsh architecture to
# MAAS architecture.
ARCH_FIX = {
    'x86_64': 'amd64/generic',
    'ppc64': 'ppc64el/generic',
    'ppc64le': 'ppc64el/generic',
    'i686': 'i386/generic',
    'aarch64': 'arm64/generic',
    's390x': 's390x/generic',
    }
ARCH_FIX_REVERSE = {
    value: key
    for key, value in ARCH_FIX.items()
}


REQUIRED_PACKAGES = [["virsh", "libvirt-clients"],
                     ["virt-login-shell", "libvirt-clients"]]


class VirshVMState:
    OFF = "shut off"
    ON = "running"
    NO_STATE = "no state"
    IDLE = "idle"
    PAUSED = "paused"
    IN_SHUTDOWN = "in shutdown"
    CRASHED = "crashed"
    PM_SUSPENDED = "pmsuspended"


VM_STATE_TO_POWER_STATE = {
    VirshVMState.OFF: "off",
    VirshVMState.ON: "on",
    VirshVMState.NO_STATE: "off",
    VirshVMState.IDLE: "off",
    VirshVMState.PAUSED: "off",
    VirshVMState.IN_SHUTDOWN: "on",
    VirshVMState.CRASHED: "off",
    VirshVMState.PM_SUSPENDED: "off",
    }


class VirshError(Exception):
    """Failure communicating to virsh. """


class VirshSSH(pexpect.spawn):

    PROMPT = r"virsh \#"
    PROMPT_SSHKEY = "(?i)are you sure you want to continue connecting"
    PROMPT_PASSWORD = "(?i)(?:password)|(?:passphrase for key)"
    PROMPT_DENIED = "(?i)permission denied"
    PROMPT_CLOSED = "(?i)connection closed by remote host"

    PROMPTS = [
        PROMPT_SSHKEY,
        PROMPT_PASSWORD,
        PROMPT,
        PROMPT_DENIED,
        PROMPT_CLOSED,
        pexpect.TIMEOUT,
        pexpect.EOF,
    ]

    I_PROMPT = PROMPTS.index(PROMPT)
    I_PROMPT_SSHKEY = PROMPTS.index(PROMPT_SSHKEY)
    I_PROMPT_PASSWORD = PROMPTS.index(PROMPT_PASSWORD)

    def __init__(self, timeout=30, maxread=2000, dom_prefix=None):
        super(VirshSSH, self).__init__(
            None, timeout=timeout, maxread=maxread,
            env=get_env_with_locale())
        self.name = '<virssh>'
        if dom_prefix is None:
            self.dom_prefix = ''
        else:
            self.dom_prefix = dom_prefix
        # Store a mapping of { machine_name: xml }.
        self.xml = {}

    def _execute(self, poweraddr):
        """Spawns the pexpect command."""
        cmd = 'virsh --connect %s' % poweraddr
        self._spawn(cmd)

    def get_machine_xml(self, machine):
        # Check if we have a cached version of the XML.
        # This is a short-lived object, so we don't need to worry about
        # expiring objects in the cache.
        if machine in self.xml:
            return self.xml[machine]

        # Grab the XML from virsh if we don't have it already.
        output = self.run(['dumpxml', machine]).strip()
        if output.startswith("error:"):
            maaslog.error("%s: Failed to get XML for machine", machine)
            return None

        # Cache the XML, since we'll need it later to reconfigure the VM.
        self.xml[machine] = output
        return output

    def login(self, poweraddr, password=None):
        """Starts connection to virsh."""
        self._execute(poweraddr)
        i = self.expect(self.PROMPTS, timeout=self.timeout)
        if i == self.I_PROMPT_SSHKEY:
            # New certificate, lets always accept but if
            # it changes it will fail to login.
            self.sendline("yes")
            i = self.expect(self.PROMPTS)
        if i == self.I_PROMPT_PASSWORD:
            # Requesting password, give it if available.
            if password is None:
                self.close()
                return False
            self.sendline(password)
            i = self.expect(self.PROMPTS)
        if i != self.I_PROMPT:
            # Something bad happened, either disconnect,
            # timeout, wrong password.
            self.close()
            return False
        return True

    def logout(self):
        """Quits the virsh session."""
        self.sendline("quit")
        self.close()

    def prompt(self, timeout=None):
        """Waits for virsh prompt."""
        if timeout is None:
            timeout = self.timeout
        i = self.expect([self.PROMPT, pexpect.TIMEOUT], timeout=timeout)
        if i == 1:
            return False
        return True

    def run(self, args):
        cmd = ' '.join(args)
        self.sendline(cmd)
        self.prompt()
        result = self.before.decode("utf-8").splitlines()
        return '\n'.join(result[1:])

    def get_column_values(self, data, keys):
        """Return tuple of column value tuples based off keys."""
        data = data.strip().splitlines()
        cols = data[0].split()
        indexes = []
        # Look for column headers matching keys.
        for k in keys:
            try:
                indexes.append(
                    cols.index(k))
            except:
                # key was not found, continue searching.
                continue
        col_values = []
        if len(indexes) > 0:
            # Iterate over data and return column key values.
            # Skip first two header lines.
            for line in data[2:]:
                line_values = []
                for index in indexes:
                    line_values.append(line.split()[index])
                col_values.append(tuple(line_values))
        return tuple(col_values)

    def get_key_value(self, data, key):
        """Return value based off of key."""
        if data is not None:
            data = data.strip().splitlines()
            for d in data:
                if key == d.split(':')[0].strip():
                    return d.split(':')[1].strip()

    def get_key_value_unitless(self, data, key):
        """Return value based off of key with unit (if any) stripped off."""
        value = self.get_key_value(data, key)
        if value:
            return value.split()[0]

    def create_storage_pool(self):
        """Create a storage pool named `maas`."""
        commands = [
            ['pool-define-as', 'maas', 'dir',
             '- - - -', '/var/lib/libvirt/maas-images'],
            ['pool-build', 'maas'],
            ['pool-start', 'maas'],
            ['pool-autostart', 'maas']]
        for command in commands:
            output = self.run(command)
            if output.startswith('error:'):
                maaslog.error("Failed to create Pod storage pool: %s", output)
                return None

    def list_machines(self):
        """Lists all VMs by name."""
        machines = self.run(['list', '--all', '--name'])
        machines = machines.strip().splitlines()
        return [m for m in machines if m.startswith(self.dom_prefix)]

    def list_pools(self):
        """Lists all pools in the pod."""
        keys = ['Name']
        output = self.run(['pool-list'])
        pools = self.get_column_values(output, keys)
        return [p[0] for p in pools]

    def list_machine_block_devices(self, machine):
        """Lists all devices for VM."""
        keys = ['Device', 'Target', 'Source']
        output = self.run(['domblklist', machine, '--details'])
        devices = self.get_column_values(output, keys)
        return [(d[1], d[2]) for d in devices if d[0] == 'disk']

    def get_machine_state(self, machine):
        """Gets the VM state."""
        state = self.run(['domstate', machine]).strip()
        if state.startswith('error:'):
            return None
        return state

    def list_machine_mac_addresses(self, machine):
        """Gets list of mac addressess assigned to the VM."""
        output = self.run(['domiflist', machine]).strip()
        if output.startswith("error:"):
            maaslog.error("%s: Failed to get node MAC addresses", machine)
            return None
        # Skip first two header lines.
        output = output.splitlines()[2:]
        # Only return the last item of the line, as it is ensured that the
        # last item is the MAC Address.
        return [line.split()[-1] for line in output]

    def get_pod_cpu_count(self):
        """Gets number of CPUs in the pod."""
        output = self.run(['nodeinfo']).strip()
        cpu_count = self.get_key_value(output, "CPU(s)")
        if cpu_count is None:
            maaslog.error("Failed to get pod CPU count")
            return 0
        return int(cpu_count)

    def get_machine_cpu_count(self, machine):
        """Gets the VM CPU count."""
        output = self.run(['dominfo', machine]).strip()
        cpu_count = self.get_key_value(output, "CPU(s)")
        if cpu_count is None:
            maaslog.error("%s: Failed to get machine CPU count", machine)
            return 0
        return int(cpu_count)

    def get_pod_cpu_speed(self):
        """Gets CPU speed (MHz) in the pod."""
        output = self.run(['nodeinfo']).strip()
        cpu_speed = self.get_key_value_unitless(output, "CPU frequency")
        if cpu_speed is None:
            maaslog.error("Failed to get pod CPU speed")
            return 0
        return int(cpu_speed)

    def get_pod_memory(self):
        """Gets the total memory of the pod."""
        output = self.run(['nodeinfo']).strip()
        KiB = self.get_key_value_unitless(output, "Memory size")
        if KiB is None:
            maaslog.error("Failed to get pod memory")
            return 0
        # Memory in MiB.
        return int(int(KiB) / 1024)

    def get_machine_memory(self, machine):
        """Gets the VM memory."""
        output = self.run(['dominfo', machine]).strip()
        KiB = self.get_key_value_unitless(output, "Max memory")
        if KiB is None:
            maaslog.error("%s: Failed to get machine memory", machine)
            return 0
        # Memory in MiB.
        return int(int(KiB) / 1024)

    def get_pod_storage_pools(self, with_available=False):
        """Get the storage pools information."""
        pools = []
        for pool in self.list_pools():
            output = self.run(['pool-dumpxml', pool]).strip()
            if output is None:
                # Skip if cannot get more information.
                continue

            doc = etree.XML(output)
            evaluator = etree.XPathEvaluator(doc)
            pool_capacity = int(evaluator(XPATH_POOL_CAPACITY)[0].text)
            pool_path = evaluator(XPATH_POOL_PATH)[0].text
            pool_type = evaluator(XPATH_POOL_TYPE)[0]
            pool_uuid = evaluator(XPATH_POOL_UUID)[0].text
            pool = DiscoveredPodStoragePool(
                id=pool_uuid, name=pool, path=pool_path,
                type=pool_type, storage=pool_capacity)

            if with_available:
                # Use `setattr` because `DiscoveredPodStoragePool` doesn't have
                # an available attribute and its only needed for the driver
                # to perform calculations. This prevents this information from
                # being sent to the region, which isn't needed.
                pool_available = int(evaluator(XPATH_POOL_AVAILABLE)[0].text)
                setattr(pool, 'available', pool_available)

            pools.append(pool)
        return pools

    def get_pod_available_local_storage(self):
        """Gets the available local storage for the pod."""
        pools = self.list_pools()
        local_storage = 0
        for pool in pools:
            output = self.run(['pool-dumpxml', pool]).strip()
            if output is None:
                maaslog.error(
                    "Failed to get available pod local storage")
                return None

            doc = etree.XML(output)
            evaluator = etree.XPathEvaluator(doc)
            pool_capacity = int(evaluator(XPATH_POOL_AVAILABLE)[0].text)
            local_storage += pool_capacity
        # Local storage in bytes.
        return local_storage

    def get_machine_local_storage(self, machine, device):
        """Gets the VM local storage for device."""
        output = self.run(['domblkinfo', machine, device]).strip()
        if output is None:
            maaslog.error(
                "Failed to get available pod local storage")
            return None
        try:
            return int(self.get_key_value(output, "Capacity"))
        except TypeError:
            return None

    def get_pod_arch(self):
        """Gets architecture of the pod."""
        output = self.run(['nodeinfo']).strip()
        arch = self.get_key_value(output, "CPU model")
        if arch is None:
            maaslog.error("Failed to get pod architecture")
            raise PodInvalidResources(
                "Pod architecture is not supported: %s" % arch)
        return ARCH_FIX.get(arch, arch)

    def get_machine_arch(self, machine):
        """Gets the VM architecture."""
        output = self.get_machine_xml(machine)
        if output is None:
            maaslog.error("%s: Failed to get VM architecture", machine)
            return None

        doc = etree.XML(output)
        evaluator = etree.XPathEvaluator(doc)
        arch = evaluator(XPATH_ARCH)[0]

        # Fix architectures that need to be referenced by a different
        # name, that MAAS understands.
        return ARCH_FIX.get(arch, arch)

    def find_storage_pool(self, source, storage_pools):
        """Find the storage pool for `source`."""
        for pool in storage_pools:
            if source.startswith(pool.path):
                return pool

    def get_pod_resources(self):
        """Get the pod resources."""
        discovered_pod = DiscoveredPod(
            architectures=[], cores=0, cpu_speed=0, memory=0, local_storage=0,
            hints=DiscoveredPodHints(
                cores=0, cpu_speed=0, memory=0, local_storage=0))
        discovered_pod.architectures = [self.get_pod_arch()]
        discovered_pod.capabilities = [
            Capabilities.COMPOSABLE,
            Capabilities.DYNAMIC_LOCAL_STORAGE,
            Capabilities.OVER_COMMIT,
            Capabilities.STORAGE_POOLS,
        ]
        discovered_pod.cores = self.get_pod_cpu_count()
        discovered_pod.cpu_speed = self.get_pod_cpu_speed()
        discovered_pod.memory = self.get_pod_memory()
        discovered_pod.storage_pools = self.get_pod_storage_pools()
        discovered_pod.local_storage = sum(
            pool.storage for pool in discovered_pod.storage_pools)
        return discovered_pod

    def get_pod_hints(self):
        """Gets the discovered pod hints."""
        discovered_pod_hints = DiscoveredPodHints(
            cores=0, cpu_speed=0, memory=0, local_storage=0)
        # You can always create a domain up to the size of total cores,
        # memory, and cpu_speed even if that amount is already in use.
        # Not a good idea, but possible.
        discovered_pod_hints.cores = self.get_pod_cpu_count()
        discovered_pod_hints.cpu_speed = self.get_pod_cpu_speed()
        discovered_pod_hints.memory = self.get_pod_memory()
        discovered_pod_hints.local_storage = (
            self.get_pod_available_local_storage())
        return discovered_pod_hints

    def get_discovered_machine(
            self, machine, request=None, storage_pools=None):
        """Gets the discovered machine."""
        # Discovered machine.
        discovered_machine = DiscoveredMachine(
            architecture="", cores=0, cpu_speed=0, memory=0,
            interfaces=[], block_devices=[], tags=[])
        discovered_machine.hostname = machine
        discovered_machine.architecture = self.get_machine_arch(machine)
        discovered_machine.cores = self.get_machine_cpu_count(machine)
        discovered_machine.memory = self.get_machine_memory(machine)
        state = self.get_machine_state(machine)
        discovered_machine.power_state = VM_STATE_TO_POWER_STATE[state]
        discovered_machine.power_parameters = {
            'power_id': machine,
        }

        # Load storage pools if needed.
        if storage_pools is None:
            storage_pools = self.get_pod_storage_pools()

        # Discover block devices.
        block_devices = []
        devices = self.list_machine_block_devices(machine)
        for idx, (device, source) in enumerate(devices):
            # Block device.
            # When request is provided map the tags from the request block
            # devices to the discovered block devices. This ensures that
            # composed machine has the requested tags on the block device.
            tags = []
            if request is not None:
                tags = request.block_devices[idx].tags
            size = self.get_machine_local_storage(machine, device)
            if size is None:
                # Bug lp:1690144 - When a domain has a block device where its
                # storage path is no longer available. The domain cannot be
                # started when the storage path is missing, so we don't add it
                # to MAAS.
                maaslog.error(
                    "Unable to discover machine '%s' in virsh pod: storage "
                    "device '%s' is missing its storage backing." % (
                        machine, device))
                return None

            # Find the storage pool for this block device. Virsh doesn't
            # tell you this information.
            storage_pool = self.find_storage_pool(source, storage_pools)
            block_devices.append(
                DiscoveredMachineBlockDevice(
                    model=None, serial=None, size=size,
                    id_path="/dev/%s" % device, tags=tags,
                    storage_pool=storage_pool.id))
        discovered_machine.block_devices = block_devices

        # Discover interfaces.
        interfaces = []
        mac_addresses = self.list_machine_mac_addresses(machine)
        boot = True
        for mac in mac_addresses:
            interfaces.append(
                DiscoveredMachineInterface(
                    mac_address=mac, boot=boot))
            boot = False
        discovered_machine.interfaces = interfaces
        return discovered_machine

    def set_machine_autostart(self, machine):
        """Set machine to autostart."""
        output = self.run(['autostart', machine]).strip()
        if output.startswith("error:"):
            maaslog.error("%s: Failed to set autostart", machine)
            return False
        return True

    def configure_pxe_boot(self, machine):
        """Given the specified machine, reads the XML dump and determines
        if the boot order needs to be changed. The boot order needs to be
        changed if it isn't (network, hd), and will be changed to that if
        it is found to be set to anything else.
        """
        xml = self.get_machine_xml(machine)
        if xml is None:
            return False
        doc = etree.XML(xml)
        evaluator = etree.XPathEvaluator(doc)

        # Remove any existing <boot/> elements under <os/>.
        boot_elements = evaluator(XPATH_BOOT)

        # Skip this if the boot order is already set up how we want it to be.
        if (len(boot_elements) == 2 and
                boot_elements[0].attrib['dev'] == 'network' and
                boot_elements[1].attrib['dev'] == 'hd'):
            return True

        for element in boot_elements:
            element.getparent().remove(element)

        # Grab the <os/> element and put the <boot/> element we want in.
        os = evaluator(XPATH_OS)[0]
        os.append(etree.XML("<boot dev='network'/>"))
        os.append(etree.XML("<boot dev='hd'/>"))

        # Rewrite the XML in a temporary file to use with 'virsh define'.
        with NamedTemporaryFile() as f:
            f.write(etree.tostring(doc))
            f.write(b'\n')
            f.flush()
            output = self.run(['define', f.name])
            if output.startswith('error:'):
                maaslog.error("%s: Failed to set network boot order", machine)
                return False
            maaslog.info("%s: Successfully set network boot order", machine)
            return True

    def poweron(self, machine):
        """Poweron a VM."""
        output = self.run(['start', machine]).strip()
        if output.startswith("error:"):
            return False
        return True

    def poweroff(self, machine):
        """Poweroff a VM."""
        output = self.run(['destroy', machine]).strip()
        if output.startswith("error:"):
            return False
        return True

    def get_usable_pool(self, disk, default_pool=None):
        """Return the pool that has enough space for `disk.size`."""
        pools = self.get_pod_storage_pools(with_available=True)
        filtered_pools = [
            pool
            for pool in pools
            if pool.name in disk.tags
        ]
        if filtered_pools:
            for pool in filtered_pools:
                if disk.size <= pool.available:
                    return pool.name
            raise PodInvalidResources(
                "Not enough storage space on storage pools: %s" % (
                    ', '.join([pool.name for pool in filtered_pools])))
        if default_pool:
            filtered_pools = [
                pool
                for pool in pools
                if pool.id == default_pool
            ]
            if not filtered_pools:
                filtered_pools = [
                    pool
                    for pool in pools
                    if pool.name == default_pool
                ]
            if filtered_pools:
                default_pool = filtered_pools[0]
                if disk.size <= default_pool.available:
                    return default_pool.name
                raise PodInvalidResources(
                    "Not enough space in default storage pool: %s" % (
                        default_pool.name))
            raise VirshError(
                "Default storage pool '%s' doesn't exist." % default_pool)
        for pool in pools:
            if disk.size <= pool.available:
                return pool.name
        raise PodInvalidResources(
            "Not enough storage space on any storage pools: %s" % (
                ', '.join([pool.name for pool in pools])))

    def create_local_volume(self, disk, default_pool=None):
        """Create a local volume with `disk.size`."""
        usable_pool = self.get_usable_pool(disk, default_pool)
        if usable_pool is None:
            return None
        volume = str(uuid4())
        self.run([
            'vol-create-as', usable_pool, volume, str(disk.size),
            '--allocation', '0', '--format', 'raw'])
        return usable_pool, volume

    def delete_local_volume(self, pool, volume):
        """Delete a local volume from `pool` with `volume`."""
        self.run(['vol-delete', volume, '--pool', pool])

    def get_volume_path(self, pool, volume):
        """Return the path to the file from `pool` and `volume`."""
        output = self.run(['vol-path', volume, '--pool', pool])
        return output.strip()

    def attach_local_volume(self, domain, pool, volume, device):
        """Attach `volume` in `pool` to `domain` as `device`."""
        vol_path = self.get_volume_path(pool, volume)
        self.run([
            'attach-disk', domain, vol_path, device,
            '--targetbus', 'virtio', '--sourcetype', 'file', '--config'])

    def get_network_list(self):
        """Return the list of available networks."""
        output = self.run(['net-list', '--name'])
        return output.strip().splitlines()

    def get_best_network(self):
        """Return the best possible network."""
        networks = self.get_network_list()
        if 'maas' in networks:
            return 'maas'
        elif 'default' in networks:
            return 'default'
        elif not networks:
            raise PodInvalidResources(
                "Pod does not have a network defined. "
                "Please add a 'default' or 'maas' network.")

        return networks[0]

    def attach_interface(self, domain, network):
        """Attach new network interface on `domain` to `network`."""
        mac = generate_mac_address()
        self.run([
            'attach-interface', domain, 'network', network,
            '--mac', mac, '--model', 'virtio', '--config'])

    def get_domain_capabilities(self):
        """Return the domain capabilities.

        Determines the type and emulator of the domain to use.
        """
        try:
            # Test for KVM support first.
            xml = self.run(['domcapabilities', '--virttype', 'kvm'])
            emulator_type = 'kvm'
        except Exception:
            # Fallback to qemu support. Fail if qemu not supported.
            xml = self.run(['domcapabilities', '--virttype', 'qemu'])
            emulator_type = 'qemu'

        # XXX newell 2017-05-18 bug=1690781
        # Check to see if the XML output was an error.
        # See bug for details about why and how this can occur.
        if xml.startswith('error'):
            raise VirshError(
                "`virsh domcapabilities --virttype %s` errored.  Please "
                "verify that package qemu-kvm is installed and restart "
                "libvirt-bin service." % emulator_type)

        doc = etree.XML(xml)
        evaluator = etree.XPathEvaluator(doc)
        emulator = evaluator('/domainCapabilities/path')[0].text
        return {
            'type': emulator_type,
            'emulator': emulator,
        }

    def cleanup_disks(self, pool_vols):
        """Delete all volumes."""
        for pool, volume in pool_vols:
            try:
                self.delete_local_volume(pool, volume)
            except Exception:
                # Ignore any exception trying to cleanup.
                pass

    def get_block_name_from_idx(self, idx):
        """Calculate a block name based on the `idx`.

        Drive#  Name
        0	    vda
        25	    vdz
        26	    vdaa
        27	    vdab
        51	    vdaz
        52	    vdba
        53	    vdbb
        701	    vdzz
        702	    vdaaa
        703	    vdaab
        18277   vdzzz
        """
        name = ""
        while idx >= 0:
            name = string.ascii_lowercase[idx % 26] + name
            idx = (idx // 26) - 1
        return "vd" + name

    def create_domain(self, request, default_pool=None):
        """Create a domain based on the `request` with hostname.

        For now this just uses `get_best_network` to connect the interfaces
        of the domain to the network.
        """
        # Create all the block devices first. If cannot complete successfully
        # then fail early. The driver currently doesn't do any tag matching
        # for block devices, and is not really required for Virsh.
        created_disks = []
        for idx, disk in enumerate(request.block_devices):
            try:
                disk_info = self.create_local_volume(disk, default_pool)
            except Exception:
                self.cleanup_disks(created_disks)
                raise
            else:
                if disk_info is None:
                    raise PodInvalidResources(
                        "not enough space for disk %d." % idx)
                else:
                    created_disks.append(disk_info)

        # Construct the domain XML.
        domain_params = self.get_domain_capabilities()
        domain_params['name'] = request.hostname
        domain_params['uuid'] = str(uuid4())
        domain_params['arch'] = ARCH_FIX_REVERSE[request.architecture]
        domain_params['cores'] = str(request.cores)
        domain_params['memory'] = str(request.memory)

        # Set the template.
        if domain_params['arch'] == 'aarch64':
            # LP: #1775728 - Changes in the template are required due to
            # libvirt validation issues on the XML template. However, this
            # causes lint issues. We work around these issue by using
            # template variables instead.
            domain_params['loader'] = '/usr/share/AAVMF/AAVMF_CODE.fd'
            domain_params['nvram_path'] = '/var/lib/libvirt/qemu/nvram'
            domain_xml = DOM_TEMPLATE_ARM64.format(**domain_params)
        elif domain_params['arch'] in ('ppc64', 'ppc64le'):
            domain_xml = DOM_TEMPLATE_PPC64.format(**domain_params)
        elif domain_params['arch'] == 's390x':
            domain_xml = DOM_TEMPLATE_S390X.format(**domain_params)
        else:
            domain_xml = DOM_TEMPLATE_AMD64.format(**domain_params)

        # Define the domain in virsh.
        with NamedTemporaryFile() as f:
            f.write(domain_xml.encode('utf-8'))
            f.write(b'\n')
            f.flush()
            self.run(['define', f.name])

        # Attach the created disks in order.
        for idx, (pool, volume) in enumerate(created_disks):
            block_name = self.get_block_name_from_idx(idx)
            self.attach_local_volume(
                request.hostname, pool, volume, block_name)

        # Attach new interfaces to the best possible network.
        best_network = self.get_best_network()
        for _ in request.interfaces:
            self.attach_interface(request.hostname, best_network)

        # Set machine to autostart.
        self.set_machine_autostart(request.hostname)

        # Setup the domain to PXE boot.
        self.configure_pxe_boot(request.hostname)

        # Return the result as a discovered machine.
        return self.get_discovered_machine(request.hostname, request=request)

    def delete_domain(self, domain):
        """Delete `domain` and its volumes."""
        # Ensure that its destroyed first.
        self.run(['destroy', domain])
        # Undefine the domains and remove all storage and snapshots.
        # XXX newell 2018-02-25 bug=1741165
        # Removed the --delete-snapshots flag to workaround the volumes not
        # being deleted.  See the bug for more details.
        self.run([
            'undefine', domain, '--remove-all-storage', '--managed-save'])


class VirshPodDriver(PodDriver):

    name = 'virsh'
    description = "Virsh (virtual systems)"
    settings = [
        make_setting_field(
            'power_address', "Virsh address", required=True),
        make_setting_field(
            'power_pass', "Virsh password (optional)",
            required=False, field_type='password'),
        make_setting_field(
            'power_id', "Virsh VM ID", scope=SETTING_SCOPE.NODE,
            required=True),
    ]
    ip_extractor = make_ip_extractor(
        'power_address', IP_EXTRACTOR_PATTERNS.URL)

    def detect_missing_packages(self):
        missing_packages = set()
        for binary, package in REQUIRED_PACKAGES:
            if not shell.has_command_available(binary):
                missing_packages.add(package)
        return list(missing_packages)

    @inlineCallbacks
    def power_control_virsh(
            self, power_address, power_id, power_change,
            power_pass=None, **kwargs):
        """Powers controls a VM using virsh."""

        # Force password to None if blank, as the power control
        # script will send a blank password if one is not set.
        if power_pass == '':
            power_pass = None

        conn = VirshSSH()
        logged_in = yield deferToThread(conn.login, power_address, power_pass)
        if not logged_in:
            raise VirshError('Failed to login to virsh console.')

        state = yield deferToThread(conn.get_machine_state, power_id)
        if state is None:
            raise VirshError('%s: Failed to get power state' % power_id)

        if state == VirshVMState.OFF:
            if power_change == 'on':
                powered_on = yield deferToThread(conn.poweron, power_id)
                if powered_on is False:
                    raise VirshError('%s: Failed to power on VM' % power_id)
        elif state == VirshVMState.ON:
            if power_change == 'off':
                powered_off = yield deferToThread(conn.poweroff, power_id)
                if powered_off is False:
                    raise VirshError('%s: Failed to power off VM' % power_id)

    @inlineCallbacks
    def power_state_virsh(
            self, power_address, power_id, power_pass=None, **kwargs):
        """Return the power state for the VM using virsh."""

        # Force password to None if blank, as the power control
        # script will send a blank password if one is not set.
        if power_pass == '':
            power_pass = None

        conn = VirshSSH()
        logged_in = yield deferToThread(conn.login, power_address, power_pass)
        if not logged_in:
            raise VirshError('Failed to login to virsh console.')

        state = yield deferToThread(conn.get_machine_state, power_id)
        if state is None:
            raise VirshError('Failed to get domain: %s' % power_id)

        try:
            return VM_STATE_TO_POWER_STATE[state]
        except KeyError:
            raise VirshError('Unknown state: %s' % state)

    @asynchronous
    def power_on(self, system_id, context):
        """Power on Virsh node."""
        return self.power_control_virsh(power_change='on', **context)

    @asynchronous
    def power_off(self, system_id, context):
        """Power off Virsh node."""
        return self.power_control_virsh(power_change='off', **context)

    @asynchronous
    def power_query(self, system_id, context):
        """Power query Virsh node."""
        return self.power_state_virsh(**context)

    @inlineCallbacks
    def get_virsh_connection(self, context):
        """Connect and return the virsh connection."""
        power_address = context.get('power_address')
        power_pass = context.get('power_pass')
        # Login to Virsh console.
        conn = VirshSSH()
        logged_in = yield deferToThread(conn.login, power_address, power_pass)
        if not logged_in:
            raise VirshError('Failed to login to virsh console.')
        return conn

    @inlineCallbacks
    def discover(self, system_id, context):
        """Discover all resources.

        Returns a defer to a DiscoveredPod object.
        """
        conn = yield self.get_virsh_connection(context)

        # Check that we have at least one storage pool.  If not, create it.
        pools = yield deferToThread(conn.list_pools)
        if not len(pools):
            yield deferToThread(conn.create_storage_pool)

        # Discover pod resources.
        discovered_pod = yield deferToThread(conn.get_pod_resources)

        # Discovered pod hints.
        discovered_pod.hints = yield deferToThread(conn.get_pod_hints)

        # Discover VMs.
        machines = []
        virtual_machines = yield deferToThread(conn.list_machines)
        for vm in virtual_machines:
            discovered_machine = yield deferToThread(
                conn.get_discovered_machine, vm,
                storage_pools=discovered_pod.storage_pools)
            if discovered_machine is not None:
                discovered_machine.cpu_speed = discovered_pod.cpu_speed
                machines.append(discovered_machine)
        discovered_pod.machines = machines

        # Set KVM Pod tags to 'virtual'.
        discovered_pod.tags = ['virtual']

        # Return the DiscoveredPod
        return discovered_pod

    @inlineCallbacks
    def compose(self, system_id, context, request):
        """Compose machine."""
        conn = yield self.get_virsh_connection(context)
        default_pool = context.get(
            'default_storage_pool_id', context.get('default_storage_pool'))
        created_machine = yield deferToThread(
            conn.create_domain, request, default_pool)
        hints = yield deferToThread(conn.get_pod_hints)
        return created_machine, hints

    @inlineCallbacks
    def decompose(self, system_id, context):
        """Decompose machine."""
        conn = yield self.get_virsh_connection(context)
        yield deferToThread(conn.delete_domain, context['power_id'])
        hints = yield deferToThread(conn.get_pod_hints)
        return hints


@synchronous
@typed
def probe_virsh_and_enlist(
        user: str, poweraddr: str, password: str=None,
        prefix_filter: str=None, accept_all: bool=False,
        domain: str=None):
    """Extracts all of the VMs from virsh and enlists them
    into MAAS.

    :param user: user for the nodes.
    :param poweraddr: virsh connection string.
    :param password: password connection string.
    :param prefix_filter: only enlist nodes that have the prefix.
    :param accept_all: if True, commission enlisted nodes.
    :param domain: The domain for the node to join.
    """
    conn = VirshSSH(dom_prefix=prefix_filter)
    logged_in = conn.login(poweraddr, password)
    if not logged_in:
        raise VirshError('Failed to login to virsh console.')

    conn_list = conn.list_machines()
    for machine in conn_list:
        arch = conn.get_machine_arch(machine)
        state = conn.get_machine_state(machine)
        macs = conn.list_machine_mac_addresses(machine)

        params = {
            'power_address': poweraddr,
            'power_id': machine,
        }
        if password is not None:
            params['power_pass'] = password
        system_id = create_node(
            macs, arch, 'virsh', params, domain, hostname=machine).wait(30)

        # If the system_id is None an error occured when creating the machine.
        # Most likely the error is the node already exists.
        if system_id is None:
            continue

        # Force the machine off, as MAAS will control the machine
        # and it needs to be in a known state of off.
        if state == VirshVMState.ON:
            conn.poweroff(machine)

        conn.configure_pxe_boot(machine)

        if accept_all:
            commission_node(system_id, user).wait(30)

    conn.logout()
