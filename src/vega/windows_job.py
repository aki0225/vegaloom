from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Literal

from ctypes import wintypes

WindowsJobProbeStatus = Literal["active", "empty", "gone", "unknown"]

_ERROR_ALREADY_EXISTS = 183
_ERROR_FILE_NOT_FOUND = 2
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_TERMINATE = 0x0008
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_THREAD_SUSPEND_RESUME = 0x0002
_TH32CS_SNAPTHREAD = 0x00000004
_RESUME_THREAD_FAILED = 0xFFFFFFFF
CREATE_SUSPENDED = 0x00000004


class WindowsJobError(OSError):
    """Windows Job Object 生命周期无法被安全确认。"""


@dataclass(frozen=True)
class WindowsJobProbe:
    status: WindowsJobProbeStatus
    active_processes: int | None = None
    detail: str | None = None


class _JobBasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


class NamedWindowsJob:
    """持有一个命名 Job Object；关闭句柄不会自动终止其成员。"""

    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self._handle = handle
        self._closed = False

    @property
    def handle(self) -> int:
        if self._closed:
            raise WindowsJobError(f"Windows Job Object 已关闭：{self.name}")
        return self._handle

    def assign_process_id(self, process_id: int) -> None:
        kernel32 = _kernel32()
        process_handle = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
            False,
            process_id,
        )
        if not process_handle:
            raise _win32_error("OpenProcess for Job assignment 失败")
        raw_process_handle = _handle_value(process_handle)
        try:
            if not kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(self.handle),
                wintypes.HANDLE(raw_process_handle),
            ):
                raise _win32_error("AssignProcessToJobObject 失败")
        finally:
            _close_handle(raw_process_handle)

    def active_process_count(self) -> int:
        kernel32 = _kernel32()
        accounting = _JobBasicAccountingInformation()
        if not kernel32.QueryInformationJobObject(
            wintypes.HANDLE(self.handle),
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            None,
        ):
            raise _win32_error("QueryInformationJobObject 失败")
        return int(accounting.ActiveProcesses)

    def terminate(self, exit_code: int = 1) -> None:
        kernel32 = _kernel32()
        if not kernel32.TerminateJobObject(
            wintypes.HANDLE(self.handle),
            wintypes.UINT(exit_code),
        ):
            raise _win32_error("TerminateJobObject 失败")

    def close(self) -> None:
        if self._closed:
            return
        _close_handle(self._handle)
        self._closed = True

    def __enter__(self) -> NamedWindowsJob:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def make_job_name(execution_id: str) -> str:
    if not execution_id or any(character not in "0123456789abcdef" for character in execution_id):
        raise ValueError("execution_id 必须是非空小写十六进制字符串")
    return f"Local\\Vega-{execution_id}"


def create_named_job(name: str) -> NamedWindowsJob:
    _require_windows()
    kernel32 = _kernel32()
    ctypes.set_last_error(0)
    handle = kernel32.CreateJobObjectW(None, name)
    if not handle:
        raise _win32_error("CreateJobObjectW 失败")
    raw_handle = _handle_value(handle)
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _close_handle(raw_handle)
        raise WindowsJobError(f"Windows Job Object 名称已存在，拒绝复用：{name}")
    return NamedWindowsJob(name, raw_handle)


def open_named_job(name: str) -> NamedWindowsJob | None:
    _require_windows()
    kernel32 = _kernel32()
    access = _JOB_OBJECT_QUERY | _JOB_OBJECT_TERMINATE
    ctypes.set_last_error(0)
    handle = kernel32.OpenJobObjectW(access, False, name)
    if handle:
        return NamedWindowsJob(name, _handle_value(handle))
    error_code = ctypes.get_last_error()
    if error_code == _ERROR_FILE_NOT_FOUND:
        return None
    raise _win32_error("OpenJobObjectW 失败", error_code)


def probe_named_job(name: str) -> WindowsJobProbe:
    if os.name != "nt":
        return WindowsJobProbe("unknown", detail="当前平台无法查询 Windows Job Object")
    try:
        job = open_named_job(name)
    except WindowsJobError as exc:
        return WindowsJobProbe("unknown", detail=str(exc))
    if job is None:
        return WindowsJobProbe("gone", active_processes=0)
    try:
        active_processes = job.active_process_count()
    except WindowsJobError as exc:
        return WindowsJobProbe("unknown", detail=str(exc))
    finally:
        job.close()
    if active_processes > 0:
        return WindowsJobProbe("active", active_processes=active_processes)
    return WindowsJobProbe("empty", active_processes=0)


def resume_suspended_process(process_id: int) -> None:
    """恢复 CREATE_SUSPENDED 创建的唯一初始线程。"""

    _require_windows()
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    snapshot_value = _handle_value(snapshot)
    if snapshot_value == _INVALID_HANDLE_VALUE:
        raise _win32_error("CreateToolhelp32Snapshot 失败")
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        found_thread_id: int | None = None
        if kernel32.Thread32First(wintypes.HANDLE(snapshot_value), ctypes.byref(entry)):
            while True:
                if entry.th32OwnerProcessID == process_id:
                    found_thread_id = int(entry.th32ThreadID)
                    break
                entry.dwSize = ctypes.sizeof(entry)
                if not kernel32.Thread32Next(
                    wintypes.HANDLE(snapshot_value),
                    ctypes.byref(entry),
                ):
                    break
        if found_thread_id is None:
            raise WindowsJobError(
                f"未找到 suspended process 的初始线程：PID {process_id}"
            )
        thread_handle = kernel32.OpenThread(
            _THREAD_SUSPEND_RESUME,
            False,
            found_thread_id,
        )
        if not thread_handle:
            raise _win32_error("OpenThread 失败")
        raw_thread_handle = _handle_value(thread_handle)
        try:
            previous_suspend_count = kernel32.ResumeThread(
                wintypes.HANDLE(raw_thread_handle)
            )
            if previous_suspend_count == _RESUME_THREAD_FAILED:
                raise _win32_error("ResumeThread 失败")
            if previous_suspend_count != 1:
                raise WindowsJobError(
                    "suspended process 的初始线程挂起计数异常："
                    f"{previous_suspend_count}"
                )
        finally:
            _close_handle(raw_thread_handle)
    finally:
        _close_handle(snapshot_value)


def _kernel32() -> object:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenJobObjectW.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.OpenJobObjectW.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    ]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    ]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _close_handle(handle: int) -> None:
    if not handle:
        return
    kernel32 = _kernel32()
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def _handle_value(handle: object) -> int:
    value = ctypes.cast(handle, ctypes.c_void_p).value
    if value is None:
        return 0
    return value


def _win32_error(message: str, error_code: int | None = None) -> WindowsJobError:
    code = ctypes.get_last_error() if error_code is None else error_code
    try:
        detail = ctypes.FormatError(code).strip()
    except (AttributeError, OSError):
        detail = "unknown error"
    return WindowsJobError(f"{message}：Win32 error {code} ({detail})")


def _require_windows() -> None:
    if os.name != "nt":
        raise WindowsJobError("Windows Job Object 仅支持 Windows")
