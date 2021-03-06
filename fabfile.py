#!/usr/bin/env python

import datetime
from dateutil import parser
import json
import os
import sys
import time

import boto
import boto.s3
from boto.s3.key import Key

import requests

from fabric.api import *

BUCKET_NAME = os.environ['KIBANA_BUCKET']
PATH_PREFIX = os.environ['KIBANA_PREFIX']

ELASTIC_SEARCH_HOST = os.environ.get('KIBANA_HOST', 'localhost')
ELASTIC_SEARCH_PORT = os.environ.get('KIBANA_PORT', 9200)

###############
# Helpers

def _es_url(path):
    return 'http://{0}:{1}/{2}'.format(ELASTIC_SEARCH_HOST, ELASTIC_SEARCH_PORT, path)

def _get_boto_connection():
    return boto.connect_s3()

def _get_backup_key(format=None):
    """Get backup key given the specified format"""
    suffix = _get_time_string(format)
    return '%s/dashboard_backup_%s.json' % (PATH_PREFIX, suffix)

def _get_time_string(format):
    """Return a string of the current date using the given format"""
    return datetime.datetime.today().strftime(format).lower()

def _get_dashboards():
    """Query dashboards stored in ElasticSearch"""
    return requests.get(_es_url('/kibana-int/dashboard/_search?q=*')).text

def _create_dashboard(id, data):
    """Create a dashboard with the id and data provided"""
    requests.post(_es_url('/kibana-int/dashboard/{0}'.format(id)), data)

def _get_backup_object(key):
    """Get boto object from key"""
    conn = _get_boto_connection()
    bucket = conn.get_bucket(BUCKET_NAME)
    for item in bucket.list():
        if key == item.key:
            return item
    return None

def _get_backup_objects():
    conn = _get_boto_connection()
    bucket = conn.get_bucket(BUCKET_NAME)
    return bucket.list()

###############
# Tasks

@task
def verify_backups():
    """A sensu compatible verification of backup"""
    for item in _get_backup_objects():
        if _get_backup_key('today') == item.key:
            contents = item.get_contents_as_string()
            created_at = parser.parse(item.last_modified)
            created_ago = int(time.time()) - int(created_at.strftime("%s"))
            if len(contents) == 0:
                print "Backup is empty"
                sys.exit(1)
            elif created_ago > 60*60*24*2:
                print "No recent backup found"
                sys.exit(1)
            else:
              print "Backup is recent and non-empty"

@task
def delete_dashboards():
    """Delete dashboards from elastic search"""
    dashboards = json.loads(_get_dashboards())['hits']['hits']
    for dashboard in dashboards:
        print "Deleting  {0}".format(dashboard["_id"])
        r = requests.delete(_es_url('/kibana-int/dashboard/{0}'.format(dashboard["_id"])))
        if r.status_code != requests.codes.ok:
            print "Error: {0}".format(response)

@task
def restore_dashboards(backup_key):
    """Restores dashboards from given s3 key"""
    backup_object = _get_backup_object(backup_key)
    if backup_object:
        dashboards = json.loads(backup_object.get_contents_as_string())['hits']['hits']
        for dashboard in dashboards:
            dash_id = dashboard["_id"]
            data = dashboard['_source']
            print "Restoring dashboard {0}".format(dash_id)
            response = requests.post(_es_url('/kibana-int/dashboard/{0}'.format(dash_id)), json.dumps(data))
            if response.status_code != requests.codes.ok:
                print "Error: {0}".format(response)
    else:
        print "Could not find backup '{0}'".format(backup_key)

@task
def list_dashboards():
    """Lists the dashboard from ElasticSearch"""
    dashboards = json.loads(_get_dashboards())['hits']['hits']
    print "Dashboards:"
    for dashboard in dashboards:
        print "  {0}".format(dashboard["_id"])

@task(default=True)
def list_backups():
    """Lists backups on S3"""
    for item in _get_backup_objects():
        if PATH_PREFIX in item.key and not item.key.endswith('/'):
            size = len(item.get_contents_as_string())
            print "Kibana dashboard backup: {0} ({1} characters)".format(item.key, size)

@task
def print_backup(key):
    """Shows contents of a backup on S3"""
    print _get_backup().get_contents_as_string()

@task
def backup():
    """Backups up dashboards from Elastic Search"""
    conn = _get_boto_connection()
    bucket = conn.get_bucket(BUCKET_NAME)

    dashboard_dump = _get_dashboards()

    backup_paths = []
    backup_paths.append(_get_backup_key("%A")) # day of week
    backup_paths.append(_get_backup_key("%B")) # month of year
    backup_paths.append(_get_backup_key('today')) # today!

    for key_path in backup_paths:
        print 'Uploading backup to Amazon S3 bucket %s/%s' % (BUCKET_NAME, key_path)
        # Update the dashboard backup
        k = Key(bucket)
        k.key = key_path
        k.set_contents_from_string(dashboard_dump)

if __name__ == '__main__':
   backup()
