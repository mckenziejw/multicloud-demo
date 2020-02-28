import os
import yaml
from jinja2 import Template
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create Terraform plan for demo infrastructure")
    parser.add_argument('--template', type=str, help="the name of the terraform plan J2 template", required=True)
    parser.add_argument('--varsfile', type=str, help="the name of the deployment vars YAML file", required=True)
    parser.add_argument('--outfile', type=str, help="generated plan output file", default='out.tf')
    parser.add_argument('--scratchfile', type=str, help="file to be used to store temporary TF state for parsing", default='tfstate.json')
    args = parser.parse_args()
    workdir = os.getcwd()
    ansible_root = workdir + "/ansible"
    terra_template = args.template
    deployment_file = args.varsfile
    terra_output = args.outfile
    filename = args.scratchfile
    # Generate Terraform Plan
    f = open(terra_template, 'r')
    print("Reading template file...")
    t = Template(f.read())
    f = open(deployment_file, 'r')
    deployment = yaml.load(f)
    # Add SSH Key
    print("Importing RSA key...")
    with open(ansible_root + '/' + deployment['rsa_key_name'] + '.pub') as f:
        deployment['rsa_key'] = f.read()
    # Create user data
    print("Writing output plan...")
    with open(terra_output, 'w') as f:
        f.write(t.render(deployment))
    print("Plan complete!")