#!/usr/bin/env node
// Wraps json-schema-to-typescript with the conventions the wire-schema pipeline
// depends on: `unreachableDefinitions` (emit every $def, not just ones reachable from
// a root) and a banner marking the file generated/do-not-edit.
import { writeFileSync } from 'node:fs'
import { compileFromFile } from 'json-schema-to-typescript'

const [, , inputPath, outputPath] = process.argv

if (!inputPath || !outputPath) {
  console.error('Usage: gen-types <input-schema.json> <output.ts>')
  process.exit(1)
}

const ts = await compileFromFile(inputPath, {
  unreachableDefinitions: true,
  bannerComment:
    '/* eslint-disable -- Generated from the backend wire schema; DO NOT EDIT. */',
})

writeFileSync(outputPath, ts)
console.log(`Wrote ${outputPath}`)
