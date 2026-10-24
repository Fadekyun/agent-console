"""Actual cgroup text fixtures; no host, container, DB, mount or SSH changes."""
import importlib.util
from pathlib import Path
import unittest

from agent_console.compute_emergency_probe import BOUNDARY_SCRIPT

MODULE=Path(__file__).parents[1]/'ops/agc-worker/emergency_boundary.py'
spec=importlib.util.spec_from_file_location('boundary',MODULE)
boundary=importlib.util.module_from_spec(spec);spec.loader.exec_module(boundary)
GIB=1024**3


class HostBoundaryTests(unittest.TestCase):
    def setUp(self):
        namespace={}
        exec(BOUNDARY_SCRIPT.rsplit('\nboundary =',1)[0],namespace)
        self.probe=namespace['ct102_boundary']
        self.files={'/proc/222/cgroup':'0::/lxc/102/ns/init.scope\n',
            '/proc/222/mountinfo':'101 100 0:28 /lxc/102/ns /sys/fs/cgroup rw - cgroup2 cgroup rw\n',
            '/sys/fs/cgroup/lxc/102/memory.max':str(2*GIB),
            '/sys/fs/cgroup/lxc/102/memory.current':str(GIB//8),
            '/sys/fs/cgroup/lxc/memory.max':'max'}
    def read(self,path):
        if path not in self.files:raise FileNotFoundError(path)
        return self.files[path]
    def call(self,limit=2*GIB):return self.probe(self.read,lambda argv:'222\n',limit)
    def test_hidden_outer_boundary_is_measured_and_root_without_limit_file_is_valid(self):
        value=self.call()
        self.assertEqual(value['guest_limit_bytes'],2*GIB)
        self.assertEqual(value['guest_current_bytes'],GIB//8)
        self.assertEqual(value['guest_namespace_root'],'/lxc/102/ns')
    def test_wrong_container_mount_configured_limit_or_ancestor_fails_closed(self):
        for path,value in (('/proc/222/cgroup','0::/lxc/115/ns/init.scope'),
                           ('/proc/222/mountinfo','101 100 0:28 / /sys/fs/cgroup rw - cgroup2 cgroup rw'),
                           ('/sys/fs/cgroup/lxc/102/memory.max','max'),
                           ('/sys/fs/cgroup/lxc/102/memory.max',str(3*GIB)),
                           ('/sys/fs/cgroup/lxc/memory.max',str(GIB)),
                           ('/sys/fs/cgroup/lxc/102/memory.current','-1')):
            with self.subTest(path=path,value=value):
                previous=self.files[path];self.files[path]=value
                with self.assertRaises(ValueError):self.call()
                self.files[path]=previous
        with self.assertRaises(ValueError):self.call(GIB)
    def test_restart_during_probe_prevents_enrollment(self):
        pids=iter(['222','223'])
        with self.assertRaisesRegex(ValueError,'changed during probe'):
            self.probe(self.read,lambda argv:next(pids),2*GIB)


class WorkerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.enrollment={'version':1,'physical_host':'n100','guest_id':'lxc-102',
            'cgroup_path':'/lxc/102','namespace_root':'/lxc/102/ns',
            'memory_limit_bytes':2*GIB,'observed_at':1000}
        self.sample={'ct102_cgroup_path':'/lxc/102','ct102_namespace_root':'/lxc/102/ns',
            'ct102_memory_limit_bytes':2*GIB,'ct102_memory_current_bytes':GIB//4}
    def test_visible_max_requires_proof_and_conservative_outer_and_local_usage(self):
        value=boundary.memory_headroom(self.sample,self.enrollment,'max',GIB//8)
        self.assertEqual(value['guest_available_bytes'],2*GIB-GIB//4)
        value=boundary.memory_headroom(self.sample,self.enrollment,'max',GIB)
        self.assertEqual(value['guest_available_bytes'],GIB)
        with self.assertRaises(ValueError):boundary.memory_headroom(self.sample,None,'max',0)
    def test_tighter_visible_limit_never_inflates_headroom_or_changes_outer_identity(self):
        value=boundary.memory_headroom(self.sample,self.enrollment,str(GIB),GIB//2)
        self.assertEqual(value['guest_memory_limit_bytes'],2*GIB)
        self.assertEqual(value['guest_effective_memory_limit_bytes'],GIB)
        self.assertEqual(value['guest_available_bytes'],GIB//2)
    def test_changed_cap_mapping_bad_counter_and_stale_install_proof_refuse(self):
        for update in ({'ct102_memory_limit_bytes':GIB},{'ct102_cgroup_path':'/lxc/115'},
                       {'ct102_namespace_root':'/lxc/102'}):
            with self.assertRaises(ValueError):boundary.memory_headroom({**self.sample,**update},self.enrollment,'max',0)
        for limit in ('0','-1','bad'):
            with self.assertRaises(ValueError):boundary.memory_headroom(self.sample,self.enrollment,limit,0)
        for now in (994,1301):
            with self.assertRaises(ValueError):boundary.validate_enrollment(self.enrollment,fresh=True,now=now)
        self.assertEqual(boundary.validate_enrollment(self.enrollment,fresh=True,now=1300),self.enrollment)
        for update in ({'memory_limit_bytes':True},{'observed_at':float('nan')},{'guest_id':'lxc-115'}):
            with self.assertRaises(ValueError):boundary.validate_enrollment({**self.enrollment,**update})


if __name__=='__main__':unittest.main()
