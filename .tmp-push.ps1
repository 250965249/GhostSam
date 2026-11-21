$ErrorActionPreference = 'Stop'
$repo = '250965249/GhostSam'
$branch = 'remove-bakasu'

$base = (git rev-parse 'HEAD^').Trim()
$localTree = (git rev-parse 'HEAD^{tree}').Trim()
$parentTree = (git rev-parse 'HEAD^^{tree}').Trim()
$changes = @(git diff-tree --no-commit-id --name-only -r HEAD)

$entries = @()
foreach ($path in $changes) {
    $rel = $path.Trim('"')
    $blob = (git rev-parse "HEAD:$rel").Trim()
    $full = Join-Path $PWD $rel
    if (Test-Path -LiteralPath $full) {
        $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($full))
        $body = @{ content = $b64; encoding = 'base64' } | ConvertTo-Json -Compress
        [IO.File]::WriteAllText("$PWD/.tmp-blob.json", $body, [Text.UTF8Encoding]::new($false))
        $created = gh api --method POST "repos/$repo/git/blobs" --input .tmp-blob.json | ConvertFrom-Json
        if ($created.sha -ne $blob) { throw "blob mismatch: $rel" }
        $entries += @{ path = $rel; mode = '100644'; type = 'blob'; sha = $created.sha }
    } else {
        $entries += @{ path = $rel; mode = '100644'; type = 'blob'; sha = $null }
    }
}

$treeBody = @{ base_tree = $parentTree; tree = $entries } | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText("$PWD/.tmp-tree.json", $treeBody, [Text.UTF8Encoding]::new($false))
$tree = gh api --method POST "repos/$repo/git/trees" --input .tmp-tree.json | ConvertFrom-Json
if ($tree.sha -ne $localTree) { throw 'remote tree does not match local commit' }

$commitBody = @{
    message = ((git log -1 --format=%B) -join "`n")
    tree    = $tree.sha
    parents = @($base)
    author  = @{ name = '250965249'; email = '97288446+250965249@users.noreply.github.com'; date = (git show -s --format=%aI HEAD).Trim() }
} | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText("$PWD/.tmp-commit.json", $commitBody, [Text.UTF8Encoding]::new($false))
$commit = gh api --method POST "repos/$repo/git/commits" --input .tmp-commit.json | ConvertFrom-Json

$refBody = @{ sha = $commit.sha; force = $false } | ConvertTo-Json
[IO.File]::WriteAllText("$PWD/.tmp-ref.json", $refBody, [Text.UTF8Encoding]::new($false))
gh api --method PATCH "repos/$repo/git/refs/heads/$branch" --input .tmp-ref.json --jq '.ref + " " + .object.sha' 2>$null
if ($LASTEXITCODE -ne 0) {
    $create = @{ ref = "refs/heads/$branch"; sha = $commit.sha } | ConvertTo-Json
    [IO.File]::WriteAllText("$PWD/.tmp-refc.json", $create, [Text.UTF8Encoding]::new($false))
    gh api --method POST "repos/$repo/git/refs" --input .tmp-refc.json --jq '.ref + " " + .object.sha'
    if ($LASTEXITCODE -ne 0) { throw 'branch create failed' }
}
Remove-Item .tmp-blob.json, .tmp-tree.json, .tmp-commit.json, .tmp-ref.json, .tmp-refc.json -ErrorAction SilentlyContinue
Write-Output "pushed $($commit.sha)"