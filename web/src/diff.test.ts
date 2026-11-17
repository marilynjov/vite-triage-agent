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
