from typing import List, Tuple, Any, Optional
from pathlib import Path
import zipfile
from tempfile import NamedTemporaryFile
from tqdm import tqdm
import logging
import re
import ctypes
from os import sep
from varc_core.systems.base_system import BaseSystem

# based on https://stackoverflow.com/questions/48897687/why-does-the-syscall-process-vm-readv-sets-errno-to-success and PymemLinux library

class IOVec(ctypes.Structure):
    _fields_ = [
        ("iov_base", ctypes.c_void_p),
        ("iov_len", ctypes.c_size_t)
    ]

class LinuxSystem(BaseSystem):
    
    def __init__(
        self,
        include_memory: bool,
        include_open: bool,
        extract_dumps: bool,
        **kwargs: Any
    ) -> None:
        super().__init__(include_memory=include_memory, include_open=include_open, extract_dumps=extract_dumps, **kwargs)
        self.libc = ctypes.CDLL("libc.so.6")
        self.process_vm_readv = self.libc.process_vm_readv
        self.process_vm_readv.args = [ # type: ignore
            ctypes.c_int, 
            ctypes.POINTER(IOVec), 
            ctypes.c_ulong, 
            ctypes.POINTER(IOVec), 
            ctypes.c_ulong, 
            ctypes.c_ulong
        ]
        self.process_vm_readv.restype = ctypes.c_ssize_t
        if self.include_memory:
            self.dump_processes()
            if self.extract_dumps:
                from varc_core.utils import dumpfile_extraction
                dumpfile_extraction.extract_dumps(Path(self.output_path))

    def parse_mem_map(self, pid: int, p_name: str) -> List[Tuple[int, int]]:
        """Returns a list of (start address, end address) tuples of the regions of process memory that are mapped
        
        
        """
        map_addresses = []
        mem_map_path = Path(f"/proc/{pid}/maps")
        try:
            with mem_map_path.open(mode="r") as mem_map:
                map_content = mem_map.readlines()
                for line in map_content:
                    line_groups = re.match(r"([a-fA-F0-9]+)-([a-fA-F0-9]+)\s(r|-)" , line)
                    if line_groups.group(3) == "r": # type: ignore # Only collecting pages that are readable
                        page_start = int(line_groups.group(1), 16) # type: ignore
                        page_end = int(line_groups.group(2), 16) # type: ignore
                        map_addresses.append((page_start, page_end))
        except FileNotFoundError:
            logging.warning(f"Could not parse memory map for {p_name} (pid {pid}). Cannot dump this process.")
            return map_addresses
        except PermissionError:
            logging.warning(f"Permission denied parsing memory map for {p_name} (pid {pid}). Cannot dump this process.")
            return map_addresses

        return map_addresses

    def read_bytes(self, pid: int, address: int, byte: int) -> Optional[bytes]:
        """Reads {byte} bytes from the base memory address {address} in the virtual memory space of process {pid}
        
        :param pid: int of the process id
        :param address: int of the addrss
        :param byte: int of current bytre

        :return: Bytes from memory location
        :rtype: bytes
        """

        buff = ctypes.create_string_buffer(byte)
        io_dst = IOVec(ctypes.cast(ctypes.byref(buff), ctypes.c_void_p), byte)
        io_src = IOVec(ctypes.c_void_p(address), byte)

        linux_syscall = self.process_vm_readv(pid, ctypes.byref(io_dst), 1, ctypes.byref(io_src), 1, 0)

        if linux_syscall == -1:
            return None

        return buff.raw

    def dump_processes(self) -> None:
        """Dumps all processes to temp files, adds temp file to output archive then removes the temp file"""
        archive_out = self.output_path
        with zipfile.ZipFile(archive_out, "a", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for proc in tqdm(self.process_info, desc="Process dump progess", unit=" procs"):
                pid = proc["Process ID"]
                p_name = proc["Name"]
                maps = self.parse_mem_map(pid, p_name)
                if not maps:
                    continue
                with NamedTemporaryFile(mode="w+b", buffering=0, delete=True) as tmpfile:
                    try:
                        for map in maps:
                            page_start = map[0]
                            page_len = map[1] - map[0]
                            mem_page_content = self.read_bytes(pid, page_start, page_len)
                            if mem_page_content:
                                tmpfile.write(mem_page_content)
                        zip_file.write(tmpfile.name, f"process_dumps{sep}{p_name}_{pid}.mem")
                    except PermissionError:
                        logging.warning(f"Permission denied opening process memory for {p_name} (pid {pid}). Cannot dump this process.")
                        continue
                    except OSError as oserror:
                        logging.warning(f"Error opening process memory page for {p_name} (pid {pid}). Error was {oserror}. Dump may be incomplete.")
                        pass
                    

        logging.info(f"Dumping processing has completed. Output file is located: {archive_out}")