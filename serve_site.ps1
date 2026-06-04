#requires -version 5.1
Write-Host "Serving site/ at http://localhost:8000" -ForegroundColor Green
Write-Host "Open http://localhost:8000/status/ in your browser" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop`n" -ForegroundColor Gray
python -m http.server 8000 -d "$PSScriptRoot\docs"
