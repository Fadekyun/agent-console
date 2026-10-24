#!/usr/bin/python3
"""Run on the authorized CT115 source host; stdout is a public host proof.

Uses only the existing pinned/read-only N100 SSH probe. Root transfers stdout
to a protected CT102 file and supplies it as install-emergency.sh argument7.
This helper installs nothing and grants no compute or owner authority.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agent_console.compute_sampler import EMERGENCY_HOST_PROBE_COMMAND, PROBE_TIMEOUT_SECONDS, _transport
from emergency_boundary import validate_enrollment

if __name__=='__main__':
    host=json.loads(_transport(EMERGENCY_HOST_PROBE_COMMAND,PROBE_TIMEOUT_SECONDS))
    result={'version':1,'physical_host':'n100','guest_id':'lxc-102',
        'cgroup_path':host['guest_cgroup_path'],'namespace_root':host['guest_namespace_root'],
        'memory_limit_bytes':host['guest_limit_bytes'],'observed_at':host['boundary_observed_at']}
    validate_enrollment(result,fresh=True)
    print(json.dumps(result,sort_keys=True))
