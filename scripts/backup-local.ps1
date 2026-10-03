<#
.SYNOPSIS
    Offline local backup of the nexgen.e-meril stack: Postgres + media + secrets
    + full git history.

.DESCRIPTION
    Writes a timestamped folder containing everything needed to rebuild this
    install on a clean machine with no network access.

    Four artefacts, because they fail in different ways:
      nexgen.dump        Postgres custom-format archive (compressed, restorable
                         table-by-table with pg_restore).
      media.tgz          User uploads from media/, EXCLUDING whisper_models.
      env.backup         .env — not in git and unrecoverable if lost.
      repo.bundle        Every branch and tag as one file, clonable offline.
      uncommitted.patch  Working-tree changes not yet committed.

.NOTES
    Why the dump is written inside the container and then copied out, rather
    than piped: PowerShell re-encodes a redirected stream, which silently
    corrupts pg_dump's binary output. The file never touches a PS pipe.

    Why whisper_models is excluded: it is 464 MB of the 479 MB in media/ and is
    re-downloadable model weights, not data. Including it makes the backup 30x
    larger for nothing. Pass -IncludeWhisperModels to override.

    Why bsdtar and not Compress-Archive: Compress-Archive aborts the whole
    archive on one unreadable or over-long path, which is exactly what the
    whisper model cache contains. tar.exe ships with Windows 10+.

.EXAMPLE
    .\scripts\backup-local.ps1
    .\scripts\backup-local.ps1 -Dest E:\offsite -KeepLast 5
#>
[CmdletBinding()]
param(
    # Deliberately outside the repo: a backup inside the tree it backs up gets
    # caught by git, by the next `podman build` context, and by its own next run.
    [string] $Dest = 'D:\nexgen-backups',
    [string] $DbContainer = 'nexgen_db',
    [string] $DbName = 'nexgen',
    [string] $DbUser = 'nexgen_user',
    [switch] $IncludeWhisperModels,
    # Adds a physical copy of the Postgres data directory (db-volume.tar.gz,
    # ~25 MB) on top of the logical dump. Only restorable to the SAME Postgres
    # major version, which is why it is not the default — see the notes above.
    [switch] $IncludeDbVolume,
    # 0 keeps everything. Otherwise prune oldest folders past this count.
    [int]    $KeepLast = 0
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$stamp  = Get-Date -Format 'yyyy-MM-dd_HHmm'
$outDir = Join-Path $Dest $stamp
New-Item -ItemType Directory -Force $outDir | Out-Null
Write-Host "Backing up to $outDir" -ForegroundColor Cyan

function Show([string] $label, [string] $path) {
    if (Test-Path $path) {
        Write-Host ("  {0,-20} {1,8:N1} MB" -f $label, ((Get-Item $path).Length / 1MB)) -ForegroundColor Green
    } else {
        Write-Host ("  {0,-20} MISSING" -f $label) -ForegroundColor Red
    }
}

# ── 1. Database ─────────────────────────────────────────────────────────────
Write-Host 'Postgres...' -NoNewline
podman exec $DbContainer pg_dump -U $DbUser -d $DbName -Fc -f /tmp/nexgen.dump
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed (exit $LASTEXITCODE)" }
podman cp "${DbContainer}:/tmp/nexgen.dump" (Join-Path $outDir 'nexgen.dump')
podman exec $DbContainer rm -f /tmp/nexgen.dump
Write-Host ' done'

# Confirm it is a readable archive, not merely a file of plausible size. A dump
# that cannot be listed cannot be restored, and finding that out during a
# recovery is the worst possible time.
$dumpPath = Join-Path $outDir 'nexgen.dump'
podman cp $dumpPath "${DbContainer}:/tmp/verify.dump"
$tables = podman exec $DbContainer sh -c 'pg_restore -l /tmp/verify.dump | grep -c "TABLE DATA"'
podman exec $DbContainer rm -f /tmp/verify.dump
if ([int]$tables -lt 1) { throw 'dump verification failed — no TABLE DATA entries' }
Write-Host "  verified: $tables tables with data" -ForegroundColor DarkGray

# ── 1b. Globals — roles and their passwords ─────────────────────────────────
# pg_dump is per-database and carries NO roles. Restoring nexgen.dump onto a
# fresh Postgres therefore fails: the nexgen_user role it grants ownership to
# does not exist yet. This file is what makes a bare-metal rebuild possible.
podman exec $DbContainer sh -c "pg_dumpall -U $DbUser --globals-only > /tmp/globals.sql"
podman cp "${DbContainer}:/tmp/globals.sql" (Join-Path $outDir 'globals.sql')
podman exec $DbContainer sh -c 'find /tmp -maxdepth 1 -name globals.sql -delete'
Write-Host '  globals: roles + passwords captured' -ForegroundColor DarkGray

# ── 1c. Optional physical copy of the data directory ────────────────────────
if ($IncludeDbVolume) {
    Write-Host 'Postgres data dir (pg_basebackup, can take several minutes)...' -NoNewline
    # pg_basebackup, NOT `tar` over /var/lib/postgresql/data. Tarring a live
    # data directory copies files mid-write and yields an archive Postgres may
    # refuse to start from; pg_basebackup takes a consistent snapshot of a
    # running server. -Xf bundles the WAL needed to make it self-contained.
    #
    # -c fast forces an immediate checkpoint. Without it Postgres spreads the
    # checkpoint over checkpoint_timeout and the command appears to hang for
    # minutes, especially just after a container restart.
    #
    # A unique target directory per run, because pg_basebackup refuses a
    # non-empty destination and an interrupted run leaves one behind that it
    # then cannot clean up itself.
    $bbDir = "/tmp/bb_$([guid]::NewGuid().ToString('N').Substring(0,8))"
    podman exec $DbContainer sh -c "pg_basebackup -U $DbUser -D $bbDir -Ft -z -Xf -c fast"
    if ($LASTEXITCODE -ne 0) { throw "pg_basebackup failed (exit $LASTEXITCODE)" }
    podman cp "${DbContainer}:$bbDir/base.tar.gz"      (Join-Path $outDir 'db-volume.tar.gz')
    podman cp "${DbContainer}:$bbDir/backup_manifest"  (Join-Path $outDir 'db-volume.manifest')
    # Left in place deliberately: $bbDir sits in the container's own /tmp, which
    # is ephemeral and discarded when the container is recreated. Deleting it
    # here would add a recursive-remove step for no benefit.
    Write-Host ' done'
}

# ── 1d. Server config ───────────────────────────────────────────────────────
# postgresql.conf and pg_hba.conf live in the data directory, so a logical dump
# carries neither. They are a few KB and cheap insurance for a rebuild.
foreach ($conf in @('postgresql.conf', 'pg_hba.conf')) {
    podman cp "${DbContainer}:/var/lib/postgresql/data/$conf" (Join-Path $outDir $conf) 2>$null
}

# ── 2. Media ────────────────────────────────────────────────────────────────
if (Test-Path (Join-Path $repo 'media')) {
    Write-Host 'Media...' -NoNewline
    $tar      = "$env:SystemRoot\System32\tar.exe"
    $mediaOut = Join-Path $outDir 'media.tgz'
    # Not $args — that is a PowerShell automatic variable and assigning to it
    # has side effects inside a function scope.
    $tarArgs = @('-czf', $mediaOut)
    if (-not $IncludeWhisperModels) { $tarArgs += '--exclude=whisper_models' }
    $tarArgs += @('-C', (Join-Path $repo 'media'), '.')
    & $tar @tarArgs
    Write-Host ' done'
}

# ── 3. Secrets ──────────────────────────────────────────────────────────────
if (Test-Path (Join-Path $repo '.env')) {
    Copy-Item (Join-Path $repo '.env') (Join-Path $outDir 'env.backup') -Force
}

# ── 4. Code ─────────────────────────────────────────────────────────────────
Write-Host 'Git...' -NoNewline
# --all covers every branch and tag; the bundle is clonable with no network.
git bundle create (Join-Path $outDir 'repo.bundle') --all 2>$null | Out-Null
# The bundle holds commits only, so uncommitted work needs its own capture.
# stderr is discarded: on a CRLF checkout git emits one "LF will be replaced"
# warning per file, which buried the real output under 50 lines of noise.
git diff HEAD 2>$null | Out-File -Encoding utf8 (Join-Path $outDir 'uncommitted.patch')
git status --porcelain 2>$null | Out-File -Encoding utf8 (Join-Path $outDir 'git-status.txt')
git rev-parse HEAD 2>$null | Out-File -Encoding utf8 (Join-Path $outDir 'git-head.txt')
Write-Host ' done'

# ── 5. Manifest ─────────────────────────────────────────────────────────────
@"
nexgen.e-meril offline backup
created      : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')
machine      : $env:COMPUTERNAME
db container : $DbContainer  ($DbName / $DbUser)
git HEAD     : $(git rev-parse --short HEAD)
git branch   : $(git rev-parse --abbrev-ref HEAD)
uncommitted  : $((git status --porcelain | Measure-Object).Count) files
whisper incl : $([bool]$IncludeWhisperModels)

db volume    : $(if ($IncludeDbVolume) { 'included (db-volume.tar.gz)' } else { 'NOT included — logical dump only' })

WHAT IS *NOT* HERE
  - staticfiles volume: regenerated by `collectstatic` on every container
    start, so it is pure derived output. Nothing to lose.
  - whisper_models: ~464 MB of re-downloadable model weights. Transcription is
    unavailable until the machine has network once. -IncludeWhisperModels to add.

RESTORE — logical (preferred; portable across Postgres versions)
  1. Roles first, or step 2 fails on a fresh server:
       podman cp globals.sql ${DbContainer}:/tmp/g.sql
       podman exec ${DbContainer} psql -U postgres -f /tmp/g.sql
  2. Database:
       podman cp nexgen.dump ${DbContainer}:/tmp/n.dump
       podman exec ${DbContainer} pg_restore -U $DbUser -d $DbName --clean --if-exists /tmp/n.dump
  3. Media:      tar -xzf media.tgz -C <repo>\media
  4. Secrets:    copy env.backup -> <repo>\.env
  5. Code:       git clone repo.bundle <repo>
                 git apply uncommitted.patch     # only if you want the WIP

RESTORE — physical (only if db-volume.tar.gz is present)
  Same Postgres major version ONLY (this backup: $(podman exec $DbContainer psql -U $DbUser -d $DbName -t -c "show server_version;" 2>`$null | ForEach-Object { $_.Trim() })).
  Stop the db container, replace the volume contents, restart:
       podman stop ${DbContainer}
       podman run --rm -v nexgene-meril_postgres_data:/data -v <backupdir>:/b `
         docker.io/library/postgres:16 sh -c 'rm -rf /data/* && tar -xzf /b/db-volume.tar.gz -C /data'
       podman start ${DbContainer}
"@ | Out-File -Encoding utf8 (Join-Path $outDir 'MANIFEST.txt')

# ── Summary ─────────────────────────────────────────────────────────────────
Write-Host "`nContents:" -ForegroundColor Cyan
Show 'nexgen.dump'       (Join-Path $outDir 'nexgen.dump')
Show 'globals.sql'       (Join-Path $outDir 'globals.sql')
if ($IncludeDbVolume) { Show 'db-volume.tar.gz' (Join-Path $outDir 'db-volume.tar.gz') }
Show 'media.tgz'         (Join-Path $outDir 'media.tgz')
Show 'repo.bundle'       (Join-Path $outDir 'repo.bundle')
Show 'uncommitted.patch' (Join-Path $outDir 'uncommitted.patch')
Show 'env.backup'        (Join-Path $outDir 'env.backup')
$total = (Get-ChildItem $outDir -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("  {0,-20} {1,8:N1} MB" -f 'TOTAL', $total) -ForegroundColor Yellow

# ── Retention ───────────────────────────────────────────────────────────────
if ($KeepLast -gt 0) {
    $old = Get-ChildItem $Dest -Directory | Sort-Object Name -Descending | Select-Object -Skip $KeepLast
    foreach ($dir in $old) {
        Remove-Item $dir.FullName -Recurse -Force
        Write-Host "pruned $($dir.Name)" -ForegroundColor DarkGray
    }
}

Write-Host "`nBackup complete: $outDir" -ForegroundColor Green
