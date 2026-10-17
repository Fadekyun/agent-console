# Quota-aware volume telemetry; safe under the non-administrator worker account.
# GetDiskSpaceInformationW explicitly separates physical and caller/quota space:
# https://learn.microsoft.com/windows/win32/api/fileapi/ns-fileapi-disk_space_information
if (-not ('AgcDiskSpace' -as [type])) {
    Add-Type @'
using System; using System.Runtime.InteropServices;
public static class AgcDiskSpace {
 [StructLayout(LayoutKind.Sequential)] public struct Information {
  public ulong actualTotal,actualAvailable,actualPoolUnavailable;
  public ulong callerTotal,callerAvailable,callerPoolUnavailable;
  public ulong used,reserved,volumeReserve,availableCommitted,poolAvailable;
  public uint sectorsPerUnit,bytesPerSector;
 }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,ExactSpelling=true)]
 public static extern int GetDiskSpaceInformationW(string root,out Information value);
 public static Information Read() {
  Information value; int status=GetDiskSpaceInformationW(@"C:\",out value);
  if(status!=0) Marshal.ThrowExceptionForHR(status);
  if(value.actualTotal==0||value.sectorsPerUnit==0||value.bytesPerSector==0||value.actualAvailable>value.actualTotal)
   throw new Exception("invalid physical disk telemetry");
  return value;
 }
}
'@
}
function Get-WorkerDiskSpace {
    $value = [AgcDiskSpace]::Read()
    $unit = [uint64]$value.sectorsPerUnit * [uint64]$value.bytesPerSector
    [pscustomobject]@{
        TotalSize = [uint64]$value.actualTotal * $unit
        AvailableFreeSpace = [uint64]$value.actualAvailable * $unit
        QuotaTotalBytes = [uint64]$value.callerTotal * $unit
        QuotaAvailableBytes = [uint64]$value.callerAvailable * $unit
        Source = 'GetDiskSpaceInformationW'
    }
}
