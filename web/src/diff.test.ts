import { expect, test } from 'vitest'
import { parseDiff } from './diff'

const TWO = `diff --git a/p/a.ts b/p/a.ts
index 1..2 100644
--- a/p/a.ts
+++ b/p/a.ts
@@ -1,2 +1,2 @@ fn
 keep
-old
+new
diff --git a/p/b.ts b/p/b.ts
--- a/p/b.ts
+++ b/p/b.ts
@@ -5 +5 @@
----x
+++y
\\ No newline at end of file
`

test('parseDiff: two files, hunks, kinds', () => {
  const files = parseDiff(TWO)
  expect(files.map((f) => f.path)).toEqual(['p/a.ts', 'p/b.ts'])
  expect(files[0].lines.map((l) => l.kind)).toEqual(['hunk', 'ctx', 'del', 'add'])
  expect(files[0].lines[0].text).toBe('@@ -1,2 +1,2 @@ fn')
  expect(files[0].lines[2].text).toBe('old')
  expect(files[1].lines.slice(1)).toEqual([
    { kind: 'del', text: '---x' },
    { kind: 'add', text: '++y' },
  ])
})

test('parseDiff: empty', () => expect(parseDiff('')).toEqual([]))

// What agent.patch.patch_to_diff stores: difflib output, no `diff --git` line.
const DIFFLIB = `--- a/p/a.ts
+++ b/p/a.ts
@@ -1,3 +1,3 @@
 keep
---x
+--y
 tail
--- a/p/b.ts
+++ b/p/b.ts
@@ -5,2 +5,3 @@
 ctx
+added
 end
`

test('parseDiff: headerless difflib output, two files', () => {
  const files = parseDiff(DIFFLIB)
  expect(files.map((f) => f.path)).toEqual(['p/a.ts', 'p/b.ts'])
  expect(files[0].lines).toEqual([
    { kind: 'hunk', text: '@@ -1,3 +1,3 @@' },
    { kind: 'ctx', text: 'keep' },
    { kind: 'del', text: '--x' },
    { kind: 'add', text: '--y' },
    { kind: 'ctx', text: 'tail' },
  ])
  expect(files[1].lines.map((l) => l.kind)).toEqual(['hunk', 'ctx', 'add', 'ctx'])
})

test('parseDiff: git deletion keeps the diff --git path', () => {
  const del = `diff --git a/p/gone.ts b/p/gone.ts
deleted file mode 100644
--- a/p/gone.ts
+++ /dev/null
@@ -1 +0,0 @@
-bye
`
  expect(parseDiff(del)).toEqual([{ path: 'p/gone.ts', lines: [
    { kind: 'hunk', text: '@@ -1 +0,0 @@' }, { kind: 'del', text: 'bye' }] }])
})
