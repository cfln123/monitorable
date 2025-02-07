#!/usr/bin/env python3

import os
import yaml
import boto3
import asyncio
import argparse
import importlib
import concurrent.futures

from yaml import YAMLError

from lib.resources import Resources
from lib.output import Output
from lib.alarms import Alarms
from pathlib import Path

# Get CLI args
parser = argparse.ArgumentParser()
parser.add_argument("--format", help="output format", action="store")
parser.add_argument("--config", help="config file", action="store", default="config.yaml")
parser.add_argument("--output", help="output config file", action="store",)
parser.add_argument("--regions", help="comma seperated list of regions to query", action="store")
parser.add_argument("--skip", help="comma seperated list of services to skip", action="store")
parser.add_argument("--tag", help="tag to group resources by", action="store")
parser.add_argument("--filter", help="tag value to limit resources to", action="store")
args = parser.parse_args()

config = {}

# Get config from config file if it exists
if Path(args.config).is_file():
    try:
        config = yaml.load(open(args.config), Loader=yaml.SafeLoader)
        if not config:
            config = {}
    except OSError as err:
        print("Error: ", err.filename, ":", err.strerror)
        exit(-1)
    except (ImportError, YAMLError) as err:
        print("YAML error", args.config, err)
        exit(-1)
    except Exception as err:
        print("Unknown error", err)
        exit(-1)

# Get list of all regions from EC2 API
regions = [region['RegionName'] for region in boto3.client('ec2', 'ap-southeast-2').describe_regions()['Regions']]

# Set default and supported output formats
supported_formats = ['audit', 'json', 'yaml', 'tags', 'cfn-monitor', 'cfn-guardian']
output_format = 'audit'

# Set default skipped services
skip = []

# Import services
services_dir = os.listdir('lib/services')
supported_services = [x.split('.py')[0] for x in services_dir if x not in ['__init__.py', '__pycache__']]
service_classes = {}
for service in supported_services:
    service_module = importlib.import_module('lib.services.' + service)
    service_classes[service] = getattr(service_module, service.capitalize())


# Error message for input validation
def input_error(arg, provided, supported):
    print(f'{provided} is not a valid {arg}\nvalid {arg}s: {supported}')
    exit(1)


# Group by tags if tag provided
if args.tag:
    tag = args.tag
    tag_group = True
    if args.filter:
        tag_filter = args.filter
    else:
        tag_filter = False
else:
    tag_group = False
    tag_filter = False

# Set format/region/skip if set in config and not provided as an argument
if 'format' in config and not args.format:
    if config['format'] in supported_formats:
        output_format = config['format']
    else:
        input_error('format', config['format'], str(supported_formats))
if 'regions' in config and not args.regions:
    if all(elem in regions for elem in config['regions']):
        regions = config['regions']
    else:
        input_error('region', str(list(set(config['regions']) - set(regions))[0]), str(regions))
if 'skip' in config and not args.skip:
    if all(elem in supported_services for elem in config['skip']):
        skip = config['skip']
    else:
        input_error('service', str(list(set(config['skip']) - set(supported_services))[0]), str(supported_services))

# Set format/region/skip if set by CLI args
if args.format:
    if args.format in supported_formats:
        output_format = args.format
    else:
        input_error('format', args.format, str(supported_formats))
if args.regions:
    arg_regions = args.regions.split(',')
    if all(elem in regions for elem in arg_regions):
        regions = arg_regions
    else:
        input_error('region', str(list(set(arg_regions) - set(regions))[0]), str(regions))
if args.skip:
    arg_skip = args.skip.split(',')
    if all(elem in supported_services for elem in arg_skip):
        skip = arg_skip
    else:
        input_error('service', str(list(set(arg_skip) - set(supported_services))[0]), str(supported_services))

# Create resources and alarm objects
resources = Resources()
alarms = Alarms()

# Get terminal size
try:
    rows, columns = os.popen('stty size', 'r').read().split()
except:
    columns = 50
print('=' * int(columns))


# Async function to get resources
async def get_resources(executor, regions):
    loop = asyncio.get_event_loop()
    blocking_tasks = []
    for region in regions:
        for service in supported_services:
            if service not in skip:
                blocking_tasks.append(loop.run_in_executor(executor, service_classes[service], region))
    for completed in asyncio.as_completed(blocking_tasks):
        resources.add(await completed)


# Async function to get alarms
async def get_alarms(executor, regions):
    loop = asyncio.get_event_loop()
    blocking_tasks = []
    for region in regions:
        blocking_tasks.append(loop.run_in_executor(executor, alarms.get, region))
    await asyncio.wait(blocking_tasks)


# Create thread pool for concurrent tasks
executor = concurrent.futures.ThreadPoolExecutor(max_workers=100)
event_loop = asyncio.get_event_loop()

# Loop over regions to scan resouces
event_loop.run_until_complete(get_resources(executor, regions))

# Loop over regions to scan alarms
if output_format == 'audit':
    event_loop.run_until_complete(get_alarms(executor, regions))

print('=' * int(columns))

# Group by tags if tag provided
if tag_group:
    resources.group_by_tag(tag)

# Filter by tag if tag and filter provided
if tag_filter:
    resources.filter_by_tag(tag_filter)

# Open file for output
if args.output:
    f = open(args.output, 'w+')

# Output in selected format
output = Output(resources, alarms, tag_group)
if output_format == 'audit':
    if args.output:
        f.write(output.audit())
    else:
        print(output.audit())
if output_format == 'json':
    if args.output:
        f.write(output.json())
    else:
        print(output.json())
if output_format == 'yaml':
    if args.output:
        f.write(output.yaml())
    else:
        print(output.yaml())
if output_format == 'tags':
    if args.output:
        f.write(output.tags())
    else:
        print(output.tags())
if output_format == 'cfn-monitor':
    if args.output:
        f.write(output.cfn_monitor())
    else:
        print(output.cfn_monitor())
if output_format == 'cfn-guardian':
    if args.output:
        f.write(output.cfn_guardian())
    else:
        print(output.cfn_guardian())

# Close output file
if args.output:
    f.close()
    print('Output written to file: ' + args.output)
    print('=' * int(columns))
