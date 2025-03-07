
"""Every get_ function must return: List[dict]

notepad.exe.mem - Dumped memory
notepad.exe.mem.log - Dumped memory log

Try to keep functions working cross-platform where possible
If it can't work cross-platform, put any platform specific code in the class that inherits this base
    e.g. In linux.py
"""
import psutil
import socket
import os
import os.path
import json
import zipfile
from typing import List
from datetime import datetime
import logging
from typing import Optional
import mss
import time

from varc_core.utils.string_manips import remove_special_characters, strip_drive


_MAX_OPEN_FILE_SIZE = 10000000 # 10 Mb max dumped filesize


class BaseSystem:
    """A 

    :param process_name: 
    :param process_id: 
    :param take_screenshot: 
    :param include_memory: 
    :param include_open: 
    :param extract_dumps: 
    """
    def __init__(
        self,
        process_name: Optional[str] = None,
        process_id: Optional[int] = None,
        take_screenshot: bool = True,
        include_memory: bool = True,
        include_open: bool = True,
        extract_dumps: bool = False,
    ) -> None:
        self.todays_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logging.info(f'Acquiring system: {self.get_machine_name()}, at {self.todays_date}')
        self.timestamp = datetime.timestamp(datetime.now())
        self.process_name = process_name
        self.process_id = process_id
        self.extract_dumps = extract_dumps
        self.include_memory = include_memory
        self.include_open = include_open
    
        if self.process_name and self.process_id:
            raise ValueError("Only one of Process name or Process ID (PID) can be used. Please re-run using one or the other.")
        self.zip_path = self.acquire_volatile()

    def get_network(self) -> List[str]:
        """Get active network connections
            
        :return: List of netsta logs
        :rtype List[string]
        """
        network = []
        try:
            connections = psutil.net_connections()
        except psutil.AccessDenied:
            logging.error("Access denied attempting to get network connections") # without sudo on osx
            connections = []
        for conn in connections:
            if conn.laddr and conn.raddr:
                syslog_date: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                process_name: str = psutil.Process(conn.pid).name()
                log_line = f"{syslog_date} {conn.laddr.ip} {conn.laddr.port} {conn.raddr.ip} {conn.raddr.port} {process_name}"
                network.append(log_line)
        return network

    def get_processes_dict(self) -> List[dict]:
        """Get processes on system, potentially filtered

        :return: List of processes as dicts
        """
        if self.process_id:
            return [psutil.Process(self.process_id).as_dict()]
        elif self.process_name:
            process_choice = []
            for proc in psutil.process_iter():
                proc_dict = proc.as_dict()
                if proc_dict["name"].lower() == self.process_name.lower():
                    process_choice.append(proc_dict)
            return process_choice
        else:
            return [proc.as_dict() for proc in psutil.process_iter()]

    def dump_loaded_files(self) -> List[str]:
        """Collects files that are open

        :return: List of filepaths that were collected
        """

        process_choice = self.get_processes_dict()

        open_files: List[str] = []
        mapped_filepaths: List[str] = []
        exe_paths: List[str] = []

        for process in process_choice:
            proc_open_files = process.get("open_files", [])
            if proc_open_files:
                open_files += [open_file.path for open_file in proc_open_files]
            proc_memory_maps = process.get("memory_maps", []) 
            if proc_memory_maps:
                mapped_filepaths += [path.path for path in proc_memory_maps]
            proc_exe = process.get("exe", [])
            if proc_exe:
                exe_paths += proc_exe

        # Combine and unique
        paths = list(set(open_files + mapped_filepaths + exe_paths))
        # only return paths that exist
        return [path for path in paths if (len(path) > 1 and os.path.exists(path) and os.path.getsize(path))]

    def get_processes(self) -> List[dict]:
        """Get running process(es) 

        :return: List of running processes - e.g. [{'pid': 1}]
        """
        process_data: List[dict] = []

        process_choice = self.get_processes_dict()

        for process in process_choice:
            creation_time = datetime.utcfromtimestamp(process["create_time"]).strftime('%Y-%m-%d %H:%M:%S')
            open_files_raw = process["open_files"]
            open_files = []
            if open_files_raw:
                for open_file in open_files_raw:
                    open_files.append(open_file.path)
            open_files_str = " ".join(open_files)
            cmd_line = ""
            # Windows
            if isinstance(process["cmdline"], str):
                cmd_line = process["cmdline"]
            # Linux, OSX
            if isinstance(process["cmdline"], List):
                cmd_line = " ".join(process["cmdline"])
            connections = []    
            if "connections" in process:
                if process["connections"]:
                    for conn in process["connections"]:
                        if conn.laddr and conn.raddr:
                            log_line = f"{time.time()} {conn.laddr.ip} {conn.laddr.port} {conn.raddr.ip} {conn.raddr.port}"
                            connections.append(log_line)

                    
            memory_maps = process.get("memory_maps", [])
            mapped_filepaths = []
            if memory_maps:
                mapped_filepaths = [path.path for path in memory_maps]

            process_data.append({"Process ID": process["pid"], "Name": process["name"], "Username": process["username"],
                                "Status": process["status"], "Executable Path": process["exe"], "Command": cmd_line,
                                "Parent ID": process["ppid"], "Creation Time": creation_time, "Open Files": open_files_str, "Connections": "\r\n".join(connections), "Mapped Filepaths": ",".join(mapped_filepaths)
                                })
        return process_data

    def dict_to_json(self, rows: List[dict]) -> str:
        """Takes a list of rows/dict and returns as a json with a CadoJsonTable header

        :param rows: The List[Dict] of row data e.g. [{'filepath': 'file.txt'}]

        :return: The Json string
        """
        table_dict = {"format": "CadoJsonTable", "rows": rows}
        return json.dumps(table_dict, sort_keys=False, indent=1)

    def get_machine_name(self) -> str:
        """Return machine name without any special characters removed

        :return: The machine name without any special characters
        """
        return remove_special_characters(socket.gethostname())

    def take_screenshot(self) -> Optional[bytes]:
        """Takes a screenshot of all connected monitors and returns the bytes of the image

        :return:  The raw image
        """
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[0] # monitors[0] is all connected monitors in one
                sct_img = sct.grab(monitor)
                png = mss.tools.to_png(sct_img.rgb, sct_img.size)
                return png
        except mss.exception.ScreenShotError:
            logging.error("Unable to take screenshot")
        return None

    def acquire_volatile(self, output_path: Optional[str] = None) -> str:
        """Acquire volatile data into a zip file
        This is called by all OS's

        :return: The filepath of the zip
        """
        self.process_info = self.get_processes()
        self.network_log = self.get_network()
        self.dumped_files = self.dump_loaded_files() if self.include_open else []
        table_data = {}
        table_data["processes"] = self.dict_to_json(self.process_info)
        open_files_dict = [{"Open File": open_file} for open_file in self.dumped_files]
        table_data["open_files"] = self.dict_to_json(open_files_dict)
        if self.take_screenshot:
            screenshot = self.take_screenshot()
        else:
            screenshot = None
        if not output_path:
            output_path = os.path.join("", f"{self.get_machine_name()}-{self.timestamp}.zip")
        # strip .zip if in filename as shutil appends to end
        archive_out =  output_path + ".zip" if not output_path.endswith(".zip") else output_path
        self.output_path = output_path
        with zipfile.ZipFile(archive_out, 'a', compression=zipfile.ZIP_DEFLATED) as zip_file:
            if screenshot:
                zip_file.writestr(f"{self.get_machine_name()}-{self.timestamp}.png", screenshot) 
            for key, value in table_data.items():
                with zip_file.open(f"{key}.json", 'w') as json_file:
                    json_file.write(value.encode())
            if self.network_log:
                logging.info("Adding Netstat Data")
                with zip_file.open("netstat.log", 'w') as network_file:
                    network_file.write("\r\n".join(self.network_log).encode())
            if self.dump_loaded_files:
                for file_path in self.dumped_files:
                    logging.info(f"Adding open file {file_path}")
                    try:
                        if os.path.getsize(file_path) > _MAX_OPEN_FILE_SIZE:
                            logging.warning(f"Skipping file as too large {file_path}")
                        else:
                            try:
                                zip_file.write(file_path, strip_drive(file_path))
                            except PermissionError:
                                logging.warn(f"Permission denied copying {file_path}")
                    except FileNotFoundError:
                        logging.warning(f"Could not open {file_path} for reading")

        return archive_out
