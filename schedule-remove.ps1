$taskName = "kk-GuelphBusStopsLayer"

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false

Write-Host "Removed scheduled task '$taskName'."
