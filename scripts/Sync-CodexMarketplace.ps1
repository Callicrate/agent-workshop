<#
.SYNOPSIS
Projects local canonical AGENTS skills (working tree) into the Codex marketplace.

.DESCRIPTION
Path-2 projector. Reads skills directly from this repository's local `skills/`
tree (no sibling repo, no Git object reads). Copies each owning plugin's skill
files into marketplaces/codex/plugins/<plugin>/skills/, derives the manifest
version cachebuster from projected content, and writes a content-based
source-lock.json. Git provides version control.
#>

[CmdletBinding()]
param(
    [string[]]$Plugin,
    [switch]$Check
)

$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$SkillsRoot = Join-Path $RepositoryRoot "skills"
$MarketplaceRoot = Join-Path $RepositoryRoot "marketplaces\codex"
$ConfigurationPath = Join-Path $RepositoryRoot "plugin-sources.json"
$MarketplaceCatalogPath = Join-Path $RepositoryRoot ".agents\plugins\marketplace.json"
$PluginsRoot = Join-Path $MarketplaceRoot "plugins"

function Get-Sha256Hex {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]]$Bytes
    )
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function ConvertTo-Utf8Bytes {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Text
    )
    return [System.Text.UTF8Encoding]::new($false).GetBytes($Text)
}

function ConvertTo-ProjectedJsonBytes {
    param(
        [Parameter(Mandatory = $true)]
        [object]$InputObject
    )
    $json = $InputObject | ConvertTo-Json -Depth 30
    $json = $json.Replace("`r`n", "`n").Replace("`r", "`n") + "`n"
    return ConvertTo-Utf8Bytes -Text $json
}

function Test-ByteEquality {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]]$Expected,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]]$Actual
    )
    if ($Expected.Length -ne $Actual.Length) { return $false }
    for ($index = 0; $index -lt $Expected.Length; $index++) {
        if ($Expected[$index] -ne $Actual[$index]) { return $false }
    }
    return $true
}

function Test-ExcludedSourcePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,
        [AllowEmptyCollection()]
        [string[]]$ExcludedPrefixes
    )
    foreach ($prefix in $ExcludedPrefixes) {
        if ($SourcePath.StartsWith($prefix, [System.StringComparison]::Ordinal)) { return $true }
    }
    return $false
}

function Assert-PathWithinPlugin {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$PluginRoot
    )
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($PluginRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPath.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside plugin root ${PluginRoot}: $resolvedPath"
    }
}

function Get-PluginRelativePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$PluginRoot
    )
    return [System.IO.Path]::GetRelativePath($PluginRoot, $Path).Replace("\", "/")
}

function Get-LocalSourceFiles {
    <#
      Enumerates working-tree files under the given source roots (relative to repo root),
      applying excluded prefixes. Returns objects with Path (repo-relative, forward slash),
      Mode (100644/100755), and Bytes.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$SourceRoots,
        [AllowEmptyCollection()]
        [string[]]$ExcludedPrefixes
    )

    $records = [System.Collections.Generic.List[object]]::new()
    foreach ($sourceRoot in $SourceRoots) {
        $absoluteRoot = Join-Path $RepositoryRoot ($sourceRoot -replace "/", "\")
        if (Test-Path -LiteralPath $absoluteRoot -PathType Container) {
            foreach ($file in Get-ChildItem -LiteralPath $absoluteRoot -Recurse -File -Force) {
                $relative = [System.IO.Path]::GetRelativePath($RepositoryRoot, $file.FullName).Replace("\", "/")
                if (Test-ExcludedSourcePath -SourcePath $relative -ExcludedPrefixes $ExcludedPrefixes) { continue }
                $mode = "100644"
                if (-not $IsWindows) {
                    $executeMask = [System.IO.UnixFileMode]::UserExecute -bor `
                        [System.IO.UnixFileMode]::GroupExecute -bor `
                        [System.IO.UnixFileMode]::OtherExecute
                    if (([System.IO.File]::GetUnixFileMode($file.FullName) -band $executeMask) -ne 0) {
                        $mode = "100755"
                    }
                }
                $records.Add([pscustomobject]@{
                        Path  = $relative
                        Mode  = $mode
                        Bytes = [System.IO.File]::ReadAllBytes($file.FullName)
                    })
            }
        }
        elseif (Test-Path -LiteralPath $absoluteRoot -PathType Leaf) {
            $relative = [System.IO.Path]::GetRelativePath($RepositoryRoot, (Get-Item -LiteralPath $absoluteRoot).FullName).Replace("\", "/")
            if (-not (Test-ExcludedSourcePath -SourcePath $relative -ExcludedPrefixes $ExcludedPrefixes)) {
                $records.Add([pscustomobject]@{
                        Path  = $relative
                        Mode  = "100644"
                        Bytes = [System.IO.File]::ReadAllBytes($absoluteRoot)
                    })
            }
        }
    }
    return @($records | Sort-Object Path)
}

function Add-ExpectedFile {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$ExpectedFiles,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]]$Bytes
    )
    $normalized = $RelativePath.Replace("\", "/")
    if ($ExpectedFiles.ContainsKey($normalized)) {
        throw "Projection produces duplicate destination path: $normalized"
    }
    $ExpectedFiles[$normalized] = $Bytes
}

function Set-ExpectedFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]]$Bytes,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[string]]$Drift
    )
    if ($Check) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            $Drift.Add("missing: $Path"); return
        }
        $actual = [System.IO.File]::ReadAllBytes($Path)
        if (-not (Test-ByteEquality -Expected $Bytes -Actual $actual)) {
            $Drift.Add("out of date: $Path")
        }
        return
    }
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $parent -Force
    }
    [System.IO.File]::WriteAllBytes($Path, $Bytes)
}

# ---- Load configuration and catalog -----------------------------------------

if (-not (Test-Path -LiteralPath $ConfigurationPath -PathType Leaf)) {
    throw "Plugin source configuration not found: $ConfigurationPath"
}
if (-not (Test-Path -LiteralPath $MarketplaceCatalogPath -PathType Leaf)) {
    throw "Codex marketplace catalog not found: $MarketplaceCatalogPath"
}
if (-not (Test-Path -LiteralPath $SkillsRoot -PathType Container)) {
    throw "Canonical skills directory not found: $SkillsRoot"
}

$configuration = Get-Content -Raw -Encoding utf8 -LiteralPath $ConfigurationPath | ConvertFrom-Json
$configuredPlugins = @($configuration.plugins)
$configuredByName = @{}
foreach ($pluginConfiguration in $configuredPlugins) {
    $configuredByName[[string]$pluginConfiguration.name] = $pluginConfiguration
}

$marketplaceCatalog = Get-Content -Raw -Encoding utf8 -LiteralPath $MarketplaceCatalogPath | ConvertFrom-Json
$marketplacePluginsByName = @{}
foreach ($marketplacePlugin in @($marketplaceCatalog.plugins)) {
    $marketplacePluginsByName[[string]$marketplacePlugin.name] = $marketplacePlugin
}

# ---- Validate ownership and taxonomy ----------------------------------------

$skillOwners = @{}
foreach ($pluginConfiguration in $configuredPlugins) {
    $pluginName = [string]$pluginConfiguration.name
    foreach ($skillName in @($pluginConfiguration.skills)) {
        if ($skillOwners.ContainsKey($skillName)) {
            throw "Canonical skill '$skillName' belongs to both '$($skillOwners[$skillName])' and '$pluginName'."
        }
        $skillOwners[$skillName] = $pluginName
    }
    if (-not $marketplacePluginsByName.ContainsKey($pluginName)) {
        throw "Projected plugin has no marketplace entry: $pluginName"
    }
    $marketplacePlugin = $marketplacePluginsByName[$pluginName]
    $expectedMarketplacePath = "./marketplaces/codex/plugins/$pluginName"
    if ([string]$marketplacePlugin.source.source -ne "local" -or
        ([string]$marketplacePlugin.source.path).Replace("\", "/") -ne $expectedMarketplacePath) {
        throw "Marketplace entry '$pluginName' must use local path '$expectedMarketplacePath'."
    }
}

$excludedSkillNames = [System.Collections.Generic.List[string]]::new()
foreach ($excludedSkill in @($configuration.excludedSkills | Where-Object { $null -ne $_ })) {
    $excludedSkillName = [string]$excludedSkill.name
    if ([string]::IsNullOrWhiteSpace($excludedSkillName) -or
        [string]::IsNullOrWhiteSpace([string]$excludedSkill.reason)) {
        throw "Every excluded canonical skill must have a name and reason."
    }
    if ($excludedSkillName -in $excludedSkillNames) {
        throw "Duplicate excluded canonical skill: $excludedSkillName"
    }
    if ($skillOwners.ContainsKey($excludedSkillName)) {
        throw "Canonical skill '$excludedSkillName' cannot be both owned and excluded."
    }
    $excludedSkillNames.Add($excludedSkillName)
}

$canonicalSkillNames = @(
    Get-ChildItem -LiteralPath $SkillsRoot -Directory | ForEach-Object { $_.Name }
)
foreach ($canonicalSkillName in $canonicalSkillNames) {
    if (-not $skillOwners.ContainsKey($canonicalSkillName) -and $canonicalSkillName -notin $excludedSkillNames) {
        throw "Canonical skill has no plugin owner or explicit exclusion: $canonicalSkillName"
    }
}
foreach ($configuredSkillName in $skillOwners.Keys) {
    if ($configuredSkillName -notin $canonicalSkillNames) {
        throw "Configured canonical skill does not exist under skills/: $configuredSkillName"
    }
}
foreach ($excludedSkillName in $excludedSkillNames) {
    if ($excludedSkillName -notin $canonicalSkillNames) {
        throw "Excluded canonical skill does not exist under skills/: $excludedSkillName"
    }
}

$selectedConfigurations = $configuredPlugins
if ($Plugin) {
    foreach ($pluginName in $Plugin) {
        if (-not $configuredByName.ContainsKey($pluginName)) {
            throw "Unknown plugin '$pluginName'. Configured plugins: $($configuredByName.Keys -join ', ')"
        }
    }
    $selectedConfigurations = @($Plugin | ForEach-Object { $configuredByName[$_] })
}

$drift = [System.Collections.Generic.List[string]]::new()

# ---- Project each plugin from the local working tree ------------------------

foreach ($pluginConfiguration in $selectedConfigurations) {
    $pluginName = [string]$pluginConfiguration.name
    $pluginRoot = Join-Path $PluginsRoot $pluginName
    $manifestPath = Join-Path $pluginRoot ".codex-plugin\plugin.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Plugin manifest not found: $manifestPath"
    }

    # Source roots: owned skills, plus optional additionalFiles/inputs.
    $sourceRoots = [System.Collections.Generic.List[string]]::new()
    foreach ($skillName in @($pluginConfiguration.skills)) {
        $sourceRoots.Add("skills/$skillName")
    }
    $additionalMappings = @{}
    foreach ($mapping in @($pluginConfiguration.additionalFiles | Where-Object { $null -ne $_ })) {
        $mappingSource = [string]$mapping.source
        $mappingDestination = [string]$mapping.destination
        if ([string]::IsNullOrWhiteSpace($mappingSource) -or
            [string]::IsNullOrWhiteSpace($mappingDestination)) {
            throw "Plugin '$pluginName' contains an incomplete additional-file mapping."
        }
        $sourceRoots.Add($mappingSource)
        $additionalMappings[$mappingSource] = $mappingDestination
    }
    foreach ($inputPath in @(
            $pluginConfiguration.inputs | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
        )) {
        $sourceRoots.Add([string]$inputPath)
    }

    $excludedPrefixes = @(
        $pluginConfiguration.excludeSourcePrefixes |
        Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } |
        ForEach-Object { [string]$_ }
    )

    $sourceFiles = @(Get-LocalSourceFiles -SourceRoots @($sourceRoots) -ExcludedPrefixes $excludedPrefixes)
    if ($sourceFiles.Count -eq 0) {
        throw "No canonical source files found for plugin '$pluginName'."
    }

    $sourcePathSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($sf in $sourceFiles) { $null = $sourcePathSet.Add($sf.Path) }

    foreach ($mapping in @($pluginConfiguration.additionalFiles | Where-Object { $null -ne $_ })) {
        if (-not $sourcePathSet.Contains([string]$mapping.source)) {
            throw "Required canonical file missing for plugin '$pluginName': $([string]$mapping.source)"
        }
    }
    foreach ($inputPath in @(
            $pluginConfiguration.inputs | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
        )) {
        if (-not $sourcePathSet.Contains([string]$inputPath)) {
            throw "Required canonical input missing for plugin '$pluginName': $inputPath"
        }
    }
    foreach ($skillName in @($pluginConfiguration.skills)) {
        if (-not $sourcePathSet.Contains("skills/$skillName/SKILL.md")) {
            throw "Canonical skill entry point missing for plugin '$pluginName': skills/$skillName/SKILL.md"
        }
    }
    $allowedSkillEntryPoints = @($pluginConfiguration.skills | ForEach-Object { "skills/$_/SKILL.md" })
    foreach ($nestedSkillPath in @($sourceFiles.Path | Where-Object { $_.EndsWith("/SKILL.md") })) {
        if ($nestedSkillPath -notin $allowedSkillEntryPoints) {
            throw "Nested skill entry point would escape plugin taxonomy in '$pluginName': $nestedSkillPath"
        }
    }

    # Build expected projected files (skills keep their repo-relative path; additionalFiles remap).
    $expectedFiles = @{}
    $expectedModes = @{}
    foreach ($entry in $sourceFiles) {
        $destinationRelative = $null
        if ($entry.Path.StartsWith("skills/", [System.StringComparison]::Ordinal)) {
            $destinationRelative = $entry.Path
        }
        elseif ($additionalMappings.ContainsKey($entry.Path)) {
            $destinationRelative = $additionalMappings[$entry.Path]
        }
        if (-not $destinationRelative) { continue }
        Add-ExpectedFile -ExpectedFiles $expectedFiles -RelativePath $destinationRelative -Bytes $entry.Bytes
        $expectedModes[$destinationRelative.Replace("\", "/")] = $entry.Mode
    }

    # graphiti-memory: derive projected MCP + retrieval settings from canonical files.
    if ([string]$pluginConfiguration.kind -eq "graphiti-memory") {
        $catalogPath = Join-Path $RepositoryRoot "mcp\servers.json"
        $catalog = Get-Content -Raw -Encoding utf8 -LiteralPath $catalogPath | ConvertFrom-Json
        $graphitiServer = $catalog.servers.graphiti
        if (-not $graphitiServer -or -not $graphitiServer.url) {
            throw "Canonical MCP catalog has no Graphiti URL: $catalogPath"
        }
        $pluginMcp = [ordered]@{ mcpServers = [ordered]@{ graphiti = [ordered]@{ url = $graphitiServer.url } } }
        Add-ExpectedFile -ExpectedFiles $expectedFiles -RelativePath ".mcp.json" `
            -Bytes (ConvertTo-ProjectedJsonBytes -InputObject $pluginMcp)
        $projectedCatalog = [ordered]@{
            _comment       = "Generated Graphiti-only projection of the canonical AGENTS MCP catalog."
            schema_version = $catalog.schema_version
            servers        = [ordered]@{ graphiti = $graphitiServer }
        }
        Add-ExpectedFile -ExpectedFiles $expectedFiles -RelativePath "mcp/servers.json" `
            -Bytes (ConvertTo-ProjectedJsonBytes -InputObject $projectedCatalog)
        $settingsExamplePath = Join-Path $RepositoryRoot "hooks\memory.settings.example.json"
        $settingsExample = Get-Content -Raw -Encoding utf8 -LiteralPath $settingsExamplePath | ConvertFrom-Json
        $retrievalSettings = [ordered]@{ graphiti = $settingsExample.graphiti; memory = $settingsExample.memory }
        Add-ExpectedFile -ExpectedFiles $expectedFiles -RelativePath "hooks/memory.settings.json" `
            -Bytes (ConvertTo-ProjectedJsonBytes -InputObject $retrievalSettings)
    }

    foreach ($relativePath in @($expectedFiles.Keys)) {
        if (-not $expectedModes.ContainsKey($relativePath)) {
            $expectedModes[$relativePath] = "100644"
        }
    }

    # Content-based source digest (Path 2: sha256 of working-tree files, not Git blobs).
    $sourceDigestMaterial = @(
        $sourceFiles | Sort-Object Path |
        ForEach-Object { "$($_.Mode) $(Get-Sha256Hex -Bytes $_.Bytes) $($_.Path)" }
    ) -join "`n"
    $sourceDigest = Get-Sha256Hex -Bytes (ConvertTo-Utf8Bytes -Text $sourceDigestMaterial)

    $pluginProjectionConfiguration = [ordered]@{
        schemaVersion = $configuration.schemaVersion
        plugin        = $pluginConfiguration
    }
    $projectionManifestDigest = Get-Sha256Hex -Bytes (
        ConvertTo-Utf8Bytes -Text ($pluginProjectionConfiguration | ConvertTo-Json -Depth 30 -Compress)
    )

    $manifestJson = Get-Content -Raw -Encoding utf8 -LiteralPath $manifestPath
    $manifest = $manifestJson | ConvertFrom-Json
    $manifestForDigest = $manifestJson | ConvertFrom-Json
    $manifestForDigest.PSObject.Properties.Remove("version")

    $cachebusterMaterial = [System.Collections.Generic.List[string]]::new()
    $cachebusterMaterial.Add("source=$sourceDigest")
    $cachebusterMaterial.Add("projection=$projectionManifestDigest")
    $cachebusterMaterial.Add("manifest=" + ($manifestForDigest | ConvertTo-Json -Depth 30 -Compress))
    foreach ($relativePath in @($expectedFiles.Keys | Sort-Object)) {
        $generatedHash = Get-Sha256Hex -Bytes $expectedFiles[$relativePath]
        $cachebusterMaterial.Add("generated:${relativePath}:$($expectedModes[$relativePath])=$generatedHash")
    }
    foreach ($relativePath in @(
            $pluginConfiguration.cachebusterFiles | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
        )) {
        $localPath = Join-Path $pluginRoot ([string]$relativePath)
        if (-not (Test-Path -LiteralPath $localPath -PathType Leaf)) {
            throw "Cachebuster input not found for plugin '$pluginName': $localPath"
        }
        $localHash = Get-Sha256Hex -Bytes ([System.IO.File]::ReadAllBytes($localPath))
        $cachebusterMaterial.Add("$relativePath=$localHash")
    }
    $projectionDigest = Get-Sha256Hex -Bytes (ConvertTo-Utf8Bytes -Text ($cachebusterMaterial -join "`n"))
    $cachebuster = $projectionDigest.Substring(0, 12)

    $configuredBaseVersion = [string]$pluginConfiguration.baseVersion
    $baseVersion = if ([string]::IsNullOrWhiteSpace($configuredBaseVersion)) {
        (([string]$manifest.version) -split '\+', 2)[0]
    }
    else { $configuredBaseVersion.Trim() }
    if (-not $baseVersion) { throw "Plugin manifest has no base version: $manifestPath" }
    if ($baseVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$') {
        throw "Plugin '$pluginName' has invalid baseVersion '$baseVersion'"
    }
    $manifest.version = "$baseVersion+agents.$cachebuster"
    $manifestBytes = ConvertTo-ProjectedJsonBytes -InputObject $manifest

    # source-lock.json (Path 2 schema: content-based, no Git commit/blob provenance).
    $generatedOutputRecords = @(
        $expectedFiles.Keys | Sort-Object | ForEach-Object {
            [ordered]@{ path = $_; mode = $expectedModes[$_]; sha256 = Get-Sha256Hex -Bytes $expectedFiles[$_] }
        }
    )
    $sourceFileRecords = @(
        $sourceFiles | Sort-Object Path | ForEach-Object {
            [ordered]@{ path = $_.Path; mode = $_.Mode; sha256 = Get-Sha256Hex -Bytes $_.Bytes }
        }
    )
    $generatedFiles = @($expectedFiles.Keys + "source-lock.json" | Sort-Object -Unique)
    $sourceLock = [ordered]@{
        schemaVersion            = 4
        plugin                   = $pluginName
        sourceRoot               = "skills/"
        sourceDigest             = $sourceDigest
        projectionManifestDigest = $projectionManifestDigest
        projectionDigest         = $projectionDigest
        cachebuster              = $cachebuster
        sourceSkills             = @($pluginConfiguration.skills)
        excludedSourcePrefixes   = $excludedPrefixes
        sourceFiles              = $sourceFileRecords
        generatedFiles           = $generatedFiles
        generatedOutputs         = $generatedOutputRecords
        generatedManifestFields  = @("version")
    }
    Add-ExpectedFile -ExpectedFiles $expectedFiles -RelativePath "source-lock.json" `
        -Bytes (ConvertTo-ProjectedJsonBytes -InputObject $sourceLock)
    $expectedModes["source-lock.json"] = "100644"

    # Determine previously generated files for stale pruning.
    $previousGeneratedFiles = @()
    $sourceLockPath = Join-Path $pluginRoot "source-lock.json"
    if (Test-Path -LiteralPath $sourceLockPath -PathType Leaf) {
        try {
            $previousLock = Get-Content -Raw -Encoding utf8 -LiteralPath $sourceLockPath | ConvertFrom-Json
            $previousGeneratedFiles = @($previousLock.generatedFiles | ForEach-Object { [string]$_ })
        }
        catch { throw "Existing source lock is invalid JSON: $sourceLockPath" }
    }

    $expectedPathSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($relativePath in $expectedFiles.Keys) { $null = $expectedPathSet.Add($relativePath) }

    # Prune unexpected files under skills/.
    $skillsPath = Join-Path $pluginRoot "skills"
    if (Test-Path -LiteralPath $skillsPath -PathType Container) {
        foreach ($currentFile in Get-ChildItem -LiteralPath $skillsPath -Recurse -File) {
            $relativePath = Get-PluginRelativePath -Path $currentFile.FullName -PluginRoot $pluginRoot
            if ($expectedPathSet.Contains($relativePath)) { continue }
            if ($Check) { $drift.Add("unexpected: $($currentFile.FullName)") }
            else {
                Assert-PathWithinPlugin -Path $currentFile.FullName -PluginRoot $pluginRoot
                Remove-Item -LiteralPath $currentFile.FullName -Force
            }
        }
    }
    foreach ($relativePath in $previousGeneratedFiles) {
        if ($expectedPathSet.Contains($relativePath)) { continue }
        $stalePath = Join-Path $pluginRoot $relativePath
        if (-not (Test-Path -LiteralPath $stalePath -PathType Leaf)) { continue }
        if ($Check) { $drift.Add("unexpected: $stalePath") }
        else {
            Assert-PathWithinPlugin -Path $stalePath -PluginRoot $pluginRoot
            Remove-Item -LiteralPath $stalePath -Force
        }
    }

    # Write (or check) all expected files plus the manifest.
    foreach ($relativePath in @($expectedFiles.Keys | Sort-Object)) {
        $destinationPath = Join-Path $pluginRoot $relativePath
        Assert-PathWithinPlugin -Path $destinationPath -PluginRoot $pluginRoot
        if ([string]::IsNullOrWhiteSpace([string]$expectedModes[$relativePath])) {
            throw "Projection mode is missing for plugin '$pluginName' output '$relativePath'."
        }
        Set-ExpectedFile -Path $destinationPath -Bytes $expectedFiles[$relativePath] -Drift $drift
    }
    Set-ExpectedFile -Path $manifestPath -Bytes $manifestBytes -Drift $drift

    if (-not $Check) {
        Write-Output "Synchronized $pluginName from local skills/ ($cachebuster)"
    }
}

if ($Check -and $drift.Count -gt 0) {
    throw "Plugin projections are out of date:`n$($drift -join [Environment]::NewLine)"
}
if ($Check) {
    $checkedNames = @($selectedConfigurations | ForEach-Object { $_.name }) -join ", "
    Write-Output "Plugin projections match local canonical sources: $checkedNames"
}
