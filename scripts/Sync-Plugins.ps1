<#
.SYNOPSIS
Synchronizes Claude, Codex, and Copilot marketplaces from committed AGENTS sources.
#>

[CmdletBinding()]
param(
        [ValidateSet("claude", "codex", "copilot")]
    [string[]]$Provider = @("claude", "codex", "copilot"),

    [string[]]$Plugin,

    [switch]$Check
)

$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$ConfigurationPath = Join-Path $RepositoryRoot "plugin-sources.json"
$CodexRoot = Join-Path $RepositoryRoot "marketplaces\codex"
$CodexCatalogPath = Join-Path $RepositoryRoot ".agents\plugins\marketplace.json"
$CodexSyncScript = Join-Path $PSScriptRoot "Sync-CodexMarketplace.ps1"

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

    if ($Expected.Length -ne $Actual.Length) {
        return $false
    }
    for ($index = 0; $index -lt $Expected.Length; $index++) {
        if ($Expected[$index] -ne $Actual[$index]) {
            return $false
        }
    }
    return $true
}

function Get-RelativePluginPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$PluginRoot
    )

    return [System.IO.Path]::GetRelativePath($PluginRoot, $Path).Replace("\", "/")
}

function Assert-PathWithinRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPath.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside ${Root}: $resolvedPath"
    }
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

    $normalizedPath = $RelativePath.Replace("\", "/")
    if ($ExpectedFiles.ContainsKey($normalizedPath)) {
        throw "Projection produces duplicate destination path: $normalizedPath"
    }
    $ExpectedFiles[$normalizedPath] = $Bytes
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
            $Drift.Add("missing: $Path")
            return
        }
        $actualBytes = [System.IO.File]::ReadAllBytes($Path)
        if (-not (Test-ByteEquality -Expected $Bytes -Actual $actualBytes)) {
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

function ConvertTo-ClaudeHooksBytes {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $configuration = Get-Content -Raw -Encoding utf8 -LiteralPath $Path | ConvertFrom-Json
    foreach ($eventProperty in $configuration.hooks.PSObject.Properties) {
        foreach ($registration in @($eventProperty.Value)) {
            foreach ($hook in @($registration.hooks)) {
                if ($hook.command) {
                    $hook.command = ([string]$hook.command).Replace(
                        '${PLUGIN_ROOT}',
                        '${CLAUDE_PLUGIN_ROOT}'
                    )
                }
                $hook.PSObject.Properties.Remove("commandWindows")
            }
        }
    }
    return ConvertTo-ProjectedJsonBytes -InputObject $configuration
}

function Get-ProviderPluginManifest {
    param(
        [Parameter(Mandatory = $true)]
        [object]$CodexManifest,

        [Parameter(Mandatory = $true)]
        [string]$SourcePluginRoot
    )

    $manifest = [ordered]@{}
    foreach ($propertyName in @(
            "name",
            "version",
            "description",
            "author",
            "homepage",
            "repository",
            "license",
            "keywords",
            "category",
            "tags"
        )) {
        $property = $CodexManifest.PSObject.Properties[$propertyName]
        if ($null -ne $property -and $null -ne $property.Value) {
            $manifest[$propertyName] = $property.Value
        }
    }
    $manifest["skills"] = "./skills/"
    if (Test-Path -LiteralPath (Join-Path $SourcePluginRoot ".mcp.json") -PathType Leaf) {
        $manifest["mcpServers"] = "./.mcp.json"
    }
    if (Test-Path -LiteralPath (Join-Path $SourcePluginRoot "hooks\hooks.json") -PathType Leaf) {
        $manifest["hooks"] = "./hooks/hooks.json"
    }
    return $manifest
}

function Get-MarketplaceOwner {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Manifest,

        [switch]$IncludeUrl
    )

    $owner = [ordered]@{
        name = [string]$Manifest.author.name
    }
    if ($Manifest.author.email) {
        $owner["email"] = [string]$Manifest.author.email
    }
    if ($IncludeUrl -and $Manifest.author.url) {
        $owner["url"] = [string]$Manifest.author.url
    }
    return $owner
}

function Get-ProviderCatalog {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("claude", "copilot")]
        [string]$ProviderName,

        [Parameter(Mandatory = $true)]
        [object[]]$PluginConfigurations,

        [Parameter(Mandatory = $true)]
        [object]$CodexCatalog
    )

    $sourcePrefix = if ($ProviderName -eq "copilot") {
        "./marketplaces/copilot/plugins"
    }
    else {
        "./marketplaces/claude/plugins"
    }
    $catalogPlugins = foreach ($pluginConfiguration in $PluginConfigurations) {
        $pluginName = [string]$pluginConfiguration.name
        $manifestPath = Join-Path $CodexRoot "plugins\$pluginName\.codex-plugin\plugin.json"
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            throw "Codex plugin manifest not found: $manifestPath"
        }
        $manifest = Get-Content -Raw -Encoding utf8 -LiteralPath $manifestPath | ConvertFrom-Json
        [ordered]@{
            name        = $pluginName
            source      = "$sourcePrefix/$pluginName"
            description = [string]$manifest.description
        }
    }

    $firstPluginName = [string]$PluginConfigurations[0].name
    $firstManifestPath = Join-Path $CodexRoot "plugins\$firstPluginName\.codex-plugin\plugin.json"
    $firstManifest = Get-Content -Raw -Encoding utf8 -LiteralPath $firstManifestPath | ConvertFrom-Json
    $description = [string]$CodexCatalog.interface.description

    if ($ProviderName -eq "claude") {
        # Claude Code's marketplace schema accepts only name, owner, and plugins at
        # the root. Emitting $schema or a top-level description makes recent Claude
        # Code releases reject the manifest, so they are intentionally omitted.
        return [ordered]@{
            name    = [string]$CodexCatalog.name
            owner   = Get-MarketplaceOwner -Manifest $firstManifest -IncludeUrl
            plugins = @($catalogPlugins)
        }
    }

    return [ordered]@{
        name     = [string]$CodexCatalog.name
        owner    = Get-MarketplaceOwner -Manifest $firstManifest
        metadata = [ordered]@{
            description = $description
            version     = "1.0.0"
        }
        plugins  = @($catalogPlugins)
    }
}

function Sync-ProviderMarketplace {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("claude", "copilot")]
        [string]$ProviderName,

        [Parameter(Mandatory = $true)]
        [object[]]$SelectedConfigurations,

        [Parameter(Mandatory = $true)]
        [object[]]$AllConfigurations,

        [Parameter(Mandatory = $true)]
        [object]$CodexCatalog
    )

    $providerRoot = Join-Path $RepositoryRoot "marketplaces\$ProviderName"
    $pluginsRoot = Join-Path $providerRoot "plugins"
    $manifestRelativePath = if ($ProviderName -eq "claude") {
        ".claude-plugin/plugin.json"
    }
    else {
        ".plugin/plugin.json"
    }
    $drift = [System.Collections.Generic.List[string]]::new()

    foreach ($pluginConfiguration in $SelectedConfigurations) {
        $pluginName = [string]$pluginConfiguration.name
        $sourcePluginRoot = Join-Path $CodexRoot "plugins\$pluginName"
        $targetPluginRoot = Join-Path $pluginsRoot $pluginName
        if (-not (Test-Path -LiteralPath $sourcePluginRoot -PathType Container)) {
            throw "Projected Codex plugin not found: $sourcePluginRoot"
        }

        $expectedFiles = @{}
        foreach ($sourceFile in Get-ChildItem -LiteralPath $sourcePluginRoot -Recurse -File) {
            $relativePath = Get-RelativePluginPath -Path $sourceFile.FullName -PluginRoot $sourcePluginRoot
            if ($relativePath -eq "README.md" -or $relativePath.StartsWith(".codex-plugin/")) {
                continue
            }
            [byte[]]$bytes = @(
                if ($ProviderName -eq "claude" -and $relativePath -eq "hooks/hooks.json") {
                    ConvertTo-ClaudeHooksBytes -Path $sourceFile.FullName
                }
                else {
                    [System.IO.File]::ReadAllBytes($sourceFile.FullName)
                }
            )
            Add-ExpectedFile -ExpectedFiles $expectedFiles -RelativePath $relativePath -Bytes $bytes
        }

        $codexManifestPath = Join-Path $sourcePluginRoot ".codex-plugin\plugin.json"
        $codexManifest = Get-Content -Raw -Encoding utf8 -LiteralPath $codexManifestPath | ConvertFrom-Json
        $providerManifest = Get-ProviderPluginManifest `
            -CodexManifest $codexManifest `
            -SourcePluginRoot $sourcePluginRoot
        Add-ExpectedFile `
            -ExpectedFiles $expectedFiles `
            -RelativePath $manifestRelativePath `
            -Bytes (ConvertTo-ProjectedJsonBytes -InputObject $providerManifest)

        $expectedPathSet = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::OrdinalIgnoreCase
        )
        foreach ($relativePath in $expectedFiles.Keys) {
            $null = $expectedPathSet.Add($relativePath)
        }

        if (Test-Path -LiteralPath $targetPluginRoot -PathType Container) {
            foreach ($currentFile in Get-ChildItem -LiteralPath $targetPluginRoot -Recurse -File) {
                $relativePath = Get-RelativePluginPath -Path $currentFile.FullName -PluginRoot $targetPluginRoot
                if ($expectedPathSet.Contains($relativePath)) {
                    continue
                }
                if ($Check) {
                    $drift.Add("unexpected: $($currentFile.FullName)")
                }
                else {
                    Assert-PathWithinRoot -Path $currentFile.FullName -Root $targetPluginRoot
                    Remove-Item -LiteralPath $currentFile.FullName -Force
                }
            }
        }

        foreach ($relativePath in @($expectedFiles.Keys | Sort-Object)) {
            $destinationPath = Join-Path $targetPluginRoot $relativePath
            Assert-PathWithinRoot -Path $destinationPath -Root $targetPluginRoot
            Set-ExpectedFile -Path $destinationPath -Bytes $expectedFiles[$relativePath] -Drift $drift
        }

        if (-not $Check -and (Test-Path -LiteralPath $targetPluginRoot -PathType Container)) {
            Get-ChildItem -LiteralPath $targetPluginRoot -Recurse -Directory |
            Sort-Object FullName -Descending |
            Where-Object { @(Get-ChildItem -LiteralPath $_.FullName -Force).Count -eq 0 } |
            Remove-Item -Force
        }
    }

    if (-not $Plugin -and (Test-Path -LiteralPath $pluginsRoot -PathType Container)) {
        $configuredNames = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::OrdinalIgnoreCase
        )
        foreach ($pluginConfiguration in $AllConfigurations) {
            $null = $configuredNames.Add([string]$pluginConfiguration.name)
        }
        foreach ($currentPluginDirectory in Get-ChildItem -LiteralPath $pluginsRoot -Directory) {
            if ($configuredNames.Contains($currentPluginDirectory.Name)) {
                continue
            }
            if ($Check) {
                $drift.Add("unexpected plugin: $($currentPluginDirectory.FullName)")
            }
            else {
                Assert-PathWithinRoot -Path $currentPluginDirectory.FullName -Root $pluginsRoot
                Remove-Item -LiteralPath $currentPluginDirectory.FullName -Recurse -Force
            }
        }
    }

    $catalog = Get-ProviderCatalog `
        -ProviderName $ProviderName `
        -PluginConfigurations $AllConfigurations `
        -CodexCatalog $CodexCatalog
    $catalogPath = if ($ProviderName -eq "claude") {
        Join-Path $RepositoryRoot ".claude-plugin/marketplace.json"
    }
    else {
        Join-Path $RepositoryRoot ".github\plugin\marketplace.json"
    }
    Set-ExpectedFile `
        -Path $catalogPath `
        -Bytes (ConvertTo-ProjectedJsonBytes -InputObject $catalog) `
        -Drift $drift

    if ($Check -and $drift.Count -gt 0) {
        throw "$ProviderName marketplace projection is out of date:`n$($drift -join [Environment]::NewLine)"
    }

    $selectedNames = @($SelectedConfigurations | ForEach-Object { $_.name }) -join ", "
    if ($Check) {
        Write-Output "$ProviderName marketplace matches the Codex projection: $selectedNames"
    }
    else {
        Write-Output "Synchronized $ProviderName marketplace: $selectedNames"
    }
}

if (-not (Test-Path -LiteralPath $ConfigurationPath -PathType Leaf)) {
    throw "Plugin source configuration not found: $ConfigurationPath"
}
if (-not (Test-Path -LiteralPath $CodexCatalogPath -PathType Leaf)) {
    throw "Codex marketplace catalog not found: $CodexCatalogPath"
}
if (-not (Test-Path -LiteralPath $CodexSyncScript -PathType Leaf)) {
    throw "Codex sync script not found: $CodexSyncScript"
}

$configuration = Get-Content -Raw -Encoding utf8 -LiteralPath $ConfigurationPath | ConvertFrom-Json
$allConfigurations = @($configuration.plugins)
$configurationsByName = @{}
foreach ($pluginConfiguration in $allConfigurations) {
    $configurationsByName[[string]$pluginConfiguration.name] = $pluginConfiguration
}

$selectedConfigurations = $allConfigurations
if ($Plugin) {
    foreach ($pluginName in $Plugin) {
        if (-not $configurationsByName.ContainsKey($pluginName)) {
            throw "Unknown plugin '$pluginName'. Configured plugins: $($configurationsByName.Keys -join ', ')"
        }
    }
    $selectedConfigurations = @($Plugin | ForEach-Object { $configurationsByName[$_] })
}

$codexParameters = @{
    Check = $Check
}
if ($Plugin) {
    $codexParameters["Plugin"] = $Plugin
}
& $CodexSyncScript @codexParameters

$codexCatalog = Get-Content -Raw -Encoding utf8 -LiteralPath $CodexCatalogPath | ConvertFrom-Json
foreach ($providerName in @("claude", "copilot")) {
    if ($providerName -notin $Provider) {
        continue
    }
    Sync-ProviderMarketplace `
        -ProviderName $providerName `
        -SelectedConfigurations $selectedConfigurations `
        -AllConfigurations $allConfigurations `
        -CodexCatalog $codexCatalog
}
