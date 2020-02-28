import os
import subprocess
import json
from pprint import pprint
import yaml
from time import sleep
from jinja2 import Template
import argparse

def getTerraformState(scratch_filename):
    refresh_process = subprocess.Popen(['terraform', 'refresh'])
    sleep(30)
    with open(scratch_filename, 'w+') as f:
        process = subprocess.Popen(['terraform state pull'], stdout=f, universal_newlines=True, shell=True)
    sleep(20)
    terra_state = dict()
    with open(scratch_filename, 'r') as f:       
        terra_state = json.loads(f.read())
    return terra_state

def generatePrefixMap(host_count, prefix = "192.168"):
    x = set()
    out = []
    for i in range(host_count):
        for j in range(host_count):
            if len({i, j}) == 2:
                x.add(frozenset([i, j]))
    hosts = range(host_count)
    count = 10
    for i in x:
        j = set(i)
        out.append((j.pop(), j.pop(), prefix + '.' + str(count) + '.'))
        count += 1
    return out

def getPublicIP(terra_state, cloud_type, instance_name, interface_index):
    if(cloud_type != "aws" and cloud_type != "gcp" and cloud_type!= "azure"):
        return false
    else:
        target_interface_id = ""
        target_interface_public_ip_id = ""
        target_interface_public_ip = ""
        if(cloud_type == "aws"):        
            for resource in terra_state['resources']:
                if(resource['type']=="aws_instance" and resource['name'] == instance_name):
                    target_interface_id = resource['instances'][0]['attributes']['network_interface'][interface_index]['network_interface_id']
            for resource in terra_state['resources']:
                if(resource['type'] == "aws_eip" and resource['instances'][0]['attributes']['network_interface'] == target_interface_id):
                    target_interface_public_ip = resource['instances'][0]['attributes']['public_ip']
        elif(cloud_type == "azure"):
            for resource in terra_state['resources']:
                if(resource['type'] == 'azurerm_virtual_machine' and resource['name'] == instance_name):
                    target_interface_id = resource['instances'][0]['attributes']['network_interface_ids'][interface_index]
            for resource in terra_state['resources']:
                if(resource['type'] == 'azurerm_network_interface' and resource['instances'][0]['attributes']['id'] == target_interface_id):
                    target_interface_public_ip_id = resource['instances'][0]['attributes']['ip_configuration'][0]['public_ip_address_id']
            for resource in terra_state['resources']:
                if(resource['type'] == 'azurerm_public_ip' and resource['instances'][0]['attributes']['id'] == target_interface_public_ip_id):
                    target_interface_public_ip = resource['instances'][0]['attributes']['ip_address']
        return str(target_interface_public_ip)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Create Terraform plan for demo infrastructure")
    #parser.add_argument('--template', type=string, help="the name of the terraform plan J2 template", required=True)
    parser.add_argument('--varsfile', type=str, help="the name of the deployment vars YAML file", required=True)
    parser.add_argument('--inventoryfile', type=str, help="the name of the generated Ansible inventory file", default='test')
    parser.add_argument('--outfile', type=str, help="generated plan output file", default='out.tf')
    parser.add_argument('--scratchfile', type=str, help="file to be used to store temporary TF state for parsing", default='tfstate.json')
    args = parser.parse_args()
    workdir = os.getcwd()
    ansible_root = workdir + "/ansible"
    #terra_template = "terra_template.j2"
    deployment_file = args.varsfile
    terra_output = args.outfile
    inventory_filename = args.inventoryfile
    filename = args.scratchfile

    inventory = {'all':{
                    'hosts':{},
                    'children':{
                        'aws': {'hosts': {}},
                        'azure': {'hosts':{}},
                        'onprem':{'hosts':{}}
                        }
                    }
            }

    vsrx_interface_order = [
        'fxp0',
        'ge-0/0/0',
        'ge-0/0/1',
        'ge-0/0/2',
        'ge-0/0/3',
        'ge-0/0/4',
        'ge-0/0/5',
        'ge-0/0/6',
        'ge-0/0/7'
    ]

    f = open(deployment_file, 'r')
    deployment = yaml.load(f)
    # Add SSH Key
    with open(ansible_root + '/' + deployment['rsa_key_name'] + '.pub') as f:
        deployment['rsa_key'] = f.read()

    ## Refresh Terra state, check for Public IPs on all instances.
    public_ips_assigned = False
    attempt_counter = 1
    while not public_ips_assigned:
        public_ips_assigned = True
        print("Waiting for Public IP assignments (attempt {0})...".format(attempt_counter))
        attempt_counter += 1
        terra_state = getTerraformState('tfstate.json')
        for i in deployment['terraform']['resources']['azure']['instances']:
            if(i['type'] == 'vsrx'):
                public_ip = getPublicIP(terra_state, 'azure', i['name'], 0)
                if public_ip == "":
                    public_ips_assigned = False

    terra_state = getTerraformState('tfstate.json')

    for instance in deployment['terraform']['resources']['aws']['instances']:
        if(instance['type']!='vsrx'):
            continue
        f = open(deployment_file, 'r')
        d = yaml.load(f)
        new_host = d['srx_configs'][instance['name']]
        # do the weird thing for nesting keys
        inventory['all']['children']['aws']['hosts'][instance['name']] = {}
        inventory['all']['hosts'][instance['name']] = {}
        inventory['all']['children']['aws']['hosts'][instance['name']]['ansible_host_ip'] = getPublicIP(terra_state, 'aws', instance['name'], 0)
        inventory['all']['hosts'][instance['name']]['ansible_host_ip'] = inventory['all']['children']['aws']['hosts'][instance['name']]['ansible_host_ip']
        new_host['mgmt_ip'] = inventory['all']['hosts'][instance['name']]['ansible_host_ip']
        inventory['all']['children']['aws']['hosts'][instance['name']]['wan_ip'] = getPublicIP(terra_state, 'aws', instance['name'], 1)
        inventory['all']['hosts'][instance['name']]['wan_ip'] = getPublicIP(terra_state, 'aws', instance['name'], 1)
        # Set interfaces for host_vars file
        new_host['interfaces'] = []

        for i, interface in enumerate(instance['interfaces']):
            # Assume unit 0 for all
            new_int = {}
            new_int['unit'] = 0
            new_int['name'] = vsrx_interface_order[i]
            if(interface['public_ip'] and not interface.get('private_ip', False)):
                new_int['address'] = 'dhcp'
            else:
                new_int['address'] = interface['private_ip']
            new_host['interfaces'].append(new_int)
        loopback = {}
        loopback['unit'] = 0
        loopback['name'] = 'lo0'
        loopback['address'] = instance['loopback_ip']
        new_host['interfaces'].append(loopback)
        new_host['username'] = instance['username']
        new_host['rsa_key_name'] = deployment['rsa_key_name']
        new_host['rsa_key'] = deployment['rsa_key']
        #new_host['ansible_ssh_private_key_file'] = deployment['rsa_key_path']
        # Add static default route (assumes first instance is the one)
        new_host['routing_instances'][0]['routing_options'] = {}
        new_host['routing_instances'][0]['routing_options']['static'] = []
        new_host['routing_instances'][0]['routing_options']['static'].append({
        'next_hop': instance['interfaces'][1]['gateway_ip'], 
        'destination_prefix': '0.0.0.0/0'
        })
        
        with open(ansible_root + "/host_vars/" + instance['name'] + ".yml", "w+") as f:
            f.write(yaml.dump(new_host, default_flow_style = False))

    for instance in deployment['terraform']['resources']['azure']['instances']:
        if(instance['type']!='vsrx'):
            continue
        f = open(deployment_file, 'r')
        d = yaml.load(f)
        new_host = d['srx_configs'][instance['name']]
        # do the weird thing for nesting keys
        inventory['all']['children']['azure']['hosts'][instance['name']] = {}
        inventory['all']['hosts'][instance['name']] = {}
        inventory['all']['children']['azure']['hosts'][instance['name']]['ansible_host_ip'] = getPublicIP(terra_state, 'azure', instance['name'], 0)
        inventory['all']['hosts'][instance['name']]['ansible_host_ip'] = inventory['all']['children']['azure']['hosts'][instance['name']]['ansible_host_ip']
        new_host['mgmt_ip'] = inventory['all']['hosts'][instance['name']]['ansible_host_ip']
        inventory['all']['children']['azure']['hosts'][instance['name']]['wan_ip'] = getPublicIP(terra_state, 'azure', instance['name'], 1)
        inventory['all']['hosts'][instance['name']]['wan_ip'] = getPublicIP(terra_state, 'azure', instance['name'], 1)
        # Set interfaces for host_vars file
        new_host['interfaces'] = []
        
        for i, interface in enumerate(instance['interfaces']):
            # Assume unit 0 for all
            new_int = {}
            new_int['unit'] = 0
            new_int['name'] = vsrx_interface_order[i]
            if(interface['public_ip'] and not interface.get('private_ip', False)):
                new_int['address'] = 'dhcp'
            else:
                new_int['address'] = interface['private_ip']
            new_host['interfaces'].append(new_int)
        loopback = {}
        loopback['unit'] = 0
        loopback['name'] = 'lo0'
        loopback['address'] = instance['loopback_ip']
        new_host['interfaces'].append(loopback)
        new_host['username'] = instance['username']
        new_host['rsa_key_name'] = deployment['rsa_key_name']
        new_host['rsa_key'] = deployment['rsa_key']
        # Add static default route (assumes first instance is the one)
        new_host['routing_instances'][0]['routing_options'] = {}
        new_host['routing_instances'][0]['routing_options']['static'] = []
        new_host['routing_instances'][0]['routing_options']['static'].append({
        'next_hop': instance['interfaces'][1]['gateway_ip'], 
        'destination_prefix': '0.0.0.0/0'
        })
        
        with open(ansible_root + "/host_vars/" + instance['name'] + ".yml", "w") as f:
            f.write(yaml.dump(new_host, default_flow_style = False))

    for instance in deployment['on_prem']['instances']:
    #    new_host = base_srx_vars
        f = open(deployment_file, 'r')
        d = yaml.load(f)
        new_host = d['srx_configs'][instance['name']]
        new_host['mgmt_ip'] = instance['mgmt_ip']
        inventory['all']['children']['onprem']['hosts'][instance['name']] = {}
        inventory['all']['hosts'][instance['name']] = {}
        inventory['all']['hosts'][instance['name']]['ansible_host_ip'] = instance['mgmt_ip']
        inventory['all']['children']['onprem']['hosts'][instance['name']]['ansible_host_ip'] = instance['mgmt_ip']
        inventory['all']['hosts'][instance['name']]['wan_ip'] = instance['public_ip']
        inventory['all']['children']['onprem']['hosts'][instance['name']]['wan_ip'] = instance['public_ip']

        new_host['interfaces'] = []
        new_host['username'] = instance['username']
        new_host['rsa_key_name'] = deployment['rsa_key_name']
        new_host['rsa_key'] = deployment['rsa_key']
        for i, interface in enumerate(instance['interfaces']):
            # Assume unit 0 for all
            new_int = {}
            new_int['unit'] = 0
            new_int['name'] = vsrx_interface_order[i]
            if(interface['public_ip'] and not interface.get('private_ip', False)):
                new_int['address'] = 'dhcp'
            else:
                new_int['address'] = interface['private_ip']
            new_host['interfaces'].append(new_int)
        loopback = {}
        loopback['unit'] = 0
        loopback['name'] = 'lo0'
        loopback['address'] = instance['loopback_ip']
        new_host['interfaces'].append(loopback)
        with open(ansible_root + "/host_vars/" + instance['name'] + ".yml", "w") as f:
            f.write(yaml.dump(new_host, default_flow_style=False))
    # Dump the inventory file
    with open(ansible_root + "/" + inventory_filename, "w") as f:
        f.write(yaml.dump(inventory, default_flow_style = False))


    # Generate IPSec configuration vars
    st_links = generatePrefixMap(len(inventory['all']['hosts']), deployment['st_prefix'])
    host_list = []
    as_counter = 65000
    # convert to a list for predictible ordering
    for host in inventory['all']['hosts'].keys():
        host_list.append({
            'name': host,
            'wan_ip': inventory['all']['hosts'][host]['wan_ip']
        })

    for i, instance in enumerate(host_list):
        host_config = {}
        unit_counter = 0
        with open(ansible_root + "/host_vars/" + instance['name'] + ".yml", "r") as f:
            host_config = yaml.load(f.read())
            for link in st_links:
                if i in link:
                    remote_node_index = 0
                    found_indices = [0, 1, 2]
                    prefix = link[2]
                    local_ip = 0              
                    if link.index(i) == 0:
                        local_ip = 1
                        remote_ip = 2 
                        remote_node_index = link[1]
                    else:
                        local_ip = 2
                        remote_ip = 1
                        remote_node_index = link[0]

                    host_config['interfaces'].append(
                    {
                        'name': 'st0',
                        'unit': unit_counter,
                        'address': prefix + str(local_ip)
                    }
                    )   
                    host_config['zones']['vpn']['interfaces'].append("st0." + str(unit_counter))
                    host_config['routing_instances'][0]['interfaces'].append("st0." + str(unit_counter))
                    # 2. Add the IKE GW configuration
                    host_config['ike']['ike_gateways'].append(
                        {
                            'name': 'gw' + str(unit_counter),
                            'external_interface': 'ge-0/0/0',
                            'address': host_list[remote_node_index]['wan_ip'],
                            'ike_policy': host_config['ike']['ike_policies'][0]['name'],
                            'local_identity': instance['name'] + '@' + deployment['terraform']['lab_domain'],
                            'remote_identity': host_list[remote_node_index]['name'] + '@' + deployment['terraform']['lab_domain']
                        }
                    )
                    # 3. Add the IPSec VPN configuration
                    host_config['ipsec']['vpns'].append(
                        {
                            'name': 'vpn' + str(unit_counter),
                            'bind_interface': 'st0.' + str(unit_counter),
                            'ike_gateway': host_config['ike']['ike_gateways'][-1]['name'],
                            'ipsec_policy': host_config['ipsec']['policies'][0]['name']
                        }
                    )
                    host_config['protocols']['bgp']['group']['ebgp']['neighbors'].append(
                        {
                            'ip':  prefix + str(remote_ip),
                            'local_as': as_counter + i,
                            'peer_as': as_counter + remote_node_index
                        }
                    )
                    host_config['as'] = as_counter + i
                    unit_counter += 1
        with open(ansible_root + "/host_vars/" + instance['name'] + ".yml", "w") as f:
            f.write(yaml.dump(host_config, default_flow_style = False))
    print("Ansible configuration setup complete. You may now run site.yml to configure devices.")

