# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import math

import IPy

from nova import db
from nova import exception
from nova import flags
from nova import models

FLAGS = flags.FLAGS

###################


def daemon_get(context, daemon_id):
    return models.Daemon.find(daemon_id)


def daemon_get_by_args(context, node_name, binary):
    return models.Daemon.find_by_args(node_name, binary)


def daemon_create(context, values):
    daemon_ref = models.Daemon(**values)
    daemon_ref.save()
    return daemon_ref


def daemon_update(context, daemon_id, values):
    daemon_ref = daemon_get(context, daemon_id)
    for (key, value) in values.iteritems():
        daemon_ref[key] = value
    daemon_ref.save()


###################


def floating_ip_allocate_address(context, node_name, project_id):
    session = models.NovaBase.get_session()
    query = session.query(models.FloatingIp).filter_by(node_name=node_name)
    query = query.filter_by(fixed_ip_id=None).with_lockmode("update")
    floating_ip_ref = query.first()
    # NOTE(vish): if with_lockmode isn't supported, as in sqlite,
    #             then this has concurrency issues
    if not floating_ip_ref:
        raise db.NoMoreAddresses()
    floating_ip_ref['project_id'] = project_id
    session.add(floating_ip_ref)
    session.commit()
    return floating_ip_ref['ip_str']


def floating_ip_fixed_ip_associate(context, floating_address, fixed_address):
    floating_ip_ref = models.FloatingIp.find_by_ip_str(floating_address)
    fixed_ip_ref = models.FixedIp.find_by_ip_str(fixed_address)
    floating_ip_ref.fixed_ip = fixed_ip_ref
    floating_ip_ref.save()


def floating_ip_disassociate(context, address):
    floating_ip_ref = models.FloatingIp.find_by_ip_str(address)
    fixed_ip_address = floating_ip_ref.fixed_ip['ip_str']
    floating_ip_ref['fixed_ip'] = None
    floating_ip_ref.save()
    return fixed_ip_address

def floating_ip_deallocate(context, address):
    floating_ip_ref = models.FloatingIp.find_by_ip_str(address)
    floating_ip_ref['project_id'] = None
    floating_ip_ref.save()

###################


def fixed_ip_allocate_address(context, network_id):
    session = models.NovaBase.get_session()
    query = session.query(models.FixedIp).filter_by(network_id=network_id)
    query = query.filter_by(reserved=False).filter_by(allocated=False)
    query = query.filter_by(leased=False).with_lockmode("update")
    fixed_ip_ref = query.first()
    # NOTE(vish): if with_lockmode isn't supported, as in sqlite,
    #             then this has concurrency issues
    if not fixed_ip_ref:
        raise db.NoMoreAddresses()
    fixed_ip_ref['allocated'] = True
    session.add(fixed_ip_ref)
    session.commit()
    return fixed_ip_ref['ip_str']


def fixed_ip_get_by_address(context, address):
    return models.FixedIp.find_by_ip_str(address)


def fixed_ip_lease(context, address):
    fixed_ip_ref = fixed_ip_get_by_address(context, address)
    if not fixed_ip_ref['allocated']:
        raise db.AddressNotAllocated(address)
    fixed_ip_ref['leased'] = True
    fixed_ip_ref.save()


def fixed_ip_release(context, address):
    fixed_ip_ref = fixed_ip_get_by_address(context, address)
    fixed_ip_ref['allocated'] = False
    fixed_ip_ref['leased'] = False
    fixed_ip_ref.save()


def fixed_ip_deallocate(context, address):
    fixed_ip_ref = fixed_ip_get_by_address(context, address)
    fixed_ip_ref['allocated'] = False
    fixed_ip_ref.save()


def fixed_ip_instance_associate(context, address, instance_id):
    fixed_ip_ref = fixed_ip_get_by_address(context, address)
    fixed_ip_ref.instance = instance_get(context, instance_id)
    fixed_ip_ref.save()


def fixed_ip_instance_disassociate(context, address):
    fixed_ip_ref = fixed_ip_get_by_address(context, address)
    fixed_ip_ref.instance = None
    fixed_ip_ref.save()


###################


def instance_create(context, values):
    instance_ref = models.Instance()
    for (key, value) in values.iteritems():
        instance_ref[key] = value
    instance_ref.save()
    return instance_ref.id


def instance_destroy(context, instance_id):
    instance_ref = instance_get(context, instance_id)
    instance_ref.delete()


def instance_get(context, instance_id):
    return models.Instance.find(instance_id)


def instance_state(context, instance_id, state, description=None):
    instance_ref = instance_get(context, instance_id)
    instance_ref.set_state(state, description)


def instance_update(context, instance_id, values):
    instance_ref = instance_get(context, instance_id)
    for (key, value) in values.iteritems():
        instance_ref[key] = value
    instance_ref.save()


###################

# NOTE(vish): is there a better place for this logic?
def network_allocate(context, project_id):
    """Set up the network"""
    db.network_ensure_indexes(context, FLAGS.num_networks)
    network_ref = db.network_create(context, {'project_id': project_id})
    network_id = network_ref['id']
    private_net = IPy.IP(FLAGS.private_range)
    index = db.network_get_index(context, network_id)
    vlan = FLAGS.vlan_start + index
    start = index * FLAGS.network_size
    significant_bits = 32 - int(math.log(FLAGS.network_size, 2))
    cidr = "%s/%s" % (private_net[start], significant_bits)
    db.network_set_cidr(context, network_id, cidr)
    net = {}
    net['kind'] = FLAGS.network_type
    net['vlan'] = vlan
    net['bridge'] = 'br%s' % vlan
    net['vpn_public_ip_str'] = FLAGS.vpn_ip
    net['vpn_public_port'] = FLAGS.vpn_start + index
    db.network_update(context, network_id, net)
    db.network_create_fixed_ips(context, network_id, FLAGS.cnt_vpn_clients)


def network_create(context, values):
    network_ref = models.Network()
    for (key, value) in values.iteritems():
        network_ref[key] = value
    network_ref.save()
    return network_ref


def network_create_fixed_ips(context, network_id, num_vpn_clients):
    network_ref = network_get(context, network_id)
    # NOTE(vish): should these be properties of the network as opposed
    #             to constants?
    BOTTOM_RESERVED = 3
    TOP_RESERVED = 1 + num_vpn_clients
    project_net = IPy.IP(network_ref['cidr'])
    num_ips = len(project_net)
    session = models.NovaBase.get_session()
    for i in range(num_ips):
        fixed_ip = models.FixedIp()
        fixed_ip.ip_str = str(project_net[i])
        if i < BOTTOM_RESERVED or num_ips - i < TOP_RESERVED:
            fixed_ip['reserved'] = True
        fixed_ip['network'] = network_get(context, network_id)
        session.add(fixed_ip)
    session.commit()


def network_ensure_indexes(context, num_networks):
    if models.NetworkIndex.count() == 0:
        session = models.NovaBase.get_session()
        for i in range(num_networks):
            network_index = models.NetworkIndex()
            network_index.index = i
            session.add(network_index)
        session.commit()


def network_destroy(context, network_id):
    network_ref = network_get(context, network_id)
    network_ref.delete()


def network_get(context, network_id):
    return models.Network.find(network_id)


def network_get_vpn_ip(context, network_id):
    # TODO(vish): possible concurrency issue here
    network = network_get(context, network_id)
    address = network['vpn_private_ip_str']
    fixed_ip = fixed_ip_get_by_address(context, address)
    if fixed_ip['allocated']:
        raise db.AddressAlreadyAllocated()
    db.fixed_ip_allocate(context, {'allocated': True})


def network_get_host(context, network_id):
    network_ref = network_get(context, network_id)
    return network_ref['node_name']


def network_get_index(context, network_id):
    session = models.NovaBase.get_session()
    query = session.query(models.NetworkIndex).filter_by(network_id=None)
    network_index = query.with_lockmode("update").first()
    if not network_index:
        raise db.NoMoreNetworks()
    network_index['network'] = network_get(context, network_id)
    session.add(network_index)
    session.commit()
    return network_index['index']


def network_set_cidr(context, network_id, cidr):
    network_ref = network_get(context, network_id)
    project_net = IPy.IP(cidr)
    network_ref['cidr'] = cidr
    # FIXME we can turn these into properties
    network_ref['netmask'] = str(project_net.netmask())
    network_ref['gateway'] = str(project_net[1])
    network_ref['broadcast'] = str(project_net.broadcast())
    network_ref['vpn_private_ip_str'] = str(project_net[2])


def network_set_host(context, network_id, host_id):
    session = models.NovaBase.get_session()
    # FIXME will a second request fail or wait for first to finish?
    query = session.query(models.Network).filter_by(id=network_id)
    network = query.with_lockmode("update").first()
    if not network:
        raise exception.NotFound("Couldn't find network with %s" %
                                 network_id)
    # NOTE(vish): if with_lockmode isn't supported, as in sqlite,
    #             then this has concurrency issues
    if network.node_name:
        session.commit()
        return network['node_name']
    network['node_name'] = host_id
    session.add(network)
    session.commit()
    return network['node_name']


def network_update(context, network_id, values):
    network_ref = network_get(context, network_id)
    for (key, value) in values.iteritems():
        network_ref[key] = value
    network_ref.save()


###################


def project_get_network(context, project_id):
    session = models.create_session()
    rv = session.query(models.Network).filter_by(project_id=project_id).first()
    if not rv:
        raise exception.NotFound('No network for project: %s' % project_id)
    return rv


###################


def volume_allocate_shelf_and_blade(context, volume_id):
    session = models.NovaBase.get_session()
    query = session.query(models.ExportDevice).filter_by(volume=None)
    export_device = query.with_lockmode("update").first()
    # NOTE(vish): if with_lockmode isn't supported, as in sqlite,
    #             then this has concurrency issues
    if not export_device:
        raise db.NoMoreBlades()
    export_device.volume_id = volume_id
    session.add(export_device)
    session.commit()
    return (export_device.shelf_id, export_device.blade_id)


def volume_attached(context, volume_id, instance_id, mountpoint):
    volume_ref = volume_get(context, volume_id)
    volume_ref.instance_id = instance_id
    volume_ref['status'] = 'in-use'
    volume_ref['mountpoint'] = mountpoint
    volume_ref['attach_status'] = 'attached'
    volume_ref.save()


def volume_create(context, values):
    volume_ref = models.Volume()
    for (key, value) in values.iteritems():
        volume_ref[key] = value
    volume_ref.save()
    return volume_ref.id


def volume_destroy(context, volume_id):
    volume_ref = volume_get(context, volume_id)
    volume_ref.delete()


def volume_detached(context, volume_id):
    volume_ref = volume_get(context, volume_id)
    volume_ref['instance_id'] = None
    volume_ref['mountpoint'] = None
    volume_ref['status'] = 'available'
    volume_ref['attach_status'] = 'detached'
    volume_ref.save()


def volume_get(context, volume_id):
    return models.Volume.find(volume_id)


def volume_get_shelf_and_blade(context, volume_id):
    volume_ref = volume_get(context, volume_id)
    export_device = volume_ref.export_device
    if not export_device:
        raise exception.NotFound()
    return (export_device.shelf_id, export_device.blade_id)


def volume_update(context, volume_id, values):
    volume_ref = volume_get(context, volume_id)
    for (key, value) in values.iteritems():
        volume_ref[key] = value
    volume_ref.save()
