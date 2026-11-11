import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

const isPaused = atom(
  { plugin: 'explain-aloud', key: 'isPaused' } as const,
  false,
)

// Locates this plugin's explain.py: newest installed copy in the plugin
// cache, falling back to a checkout opened as the working directory.
const FIND_SCRIPT = `
import glob, os, sys
hits = glob.glob(os.path.expanduser('~/.claude/plugins/cache/*/explain-aloud/*/scripts/explain.py'))
local = os.path.join(os.getcwd(), 'scripts', 'explain.py')
if os.path.exists(local):
    hits.append(local)
hits.sort(key=os.path.getmtime)
print(hits[-1] if hits else '', end='')
`

export const register: Register = on => {
  let cwd = ''
  let script = ''

  on('session.start', async ($, e, next) => {
    cwd = e.cwd
    const found = await $.process.run(['python3', '-c', FIND_SCRIPT])
    script = found.stdout.trim()
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !script) {
      return next(e)
    }

    const paused = await read($, isPaused)
    const { Box, Button, Text } = $.ui.resolve(e)
    const run = (args: string[]) =>
      $.process.run(['python3', script, ...args])

    return (
      <Box>
        <Text dimColor>explain-aloud </Text>
        <Button
          key="explain"
          label="🎙️ Explain"
          onPress={async () => {
            $.ui.toast('Generating spoken explanation (~15–40s)…')
            await update($, isPaused, () => false)
            await run(['--worker', '--force', '--project', cwd, '--detach'])
          }}
        />
        <Button
          key="back"
          label="⏪ 5s"
          onPress={() => run(['--back', '5'])}
        />
        <Button
          key="pause"
          label={paused ? '▶ Resume' : '⏸ Pause'}
          onPress={async () => {
            const wantPause = !paused
            await run([wantPause ? '--pause' : '--resume'])
            await update($, isPaused, () => wantPause)
          }}
        />
        <Button
          key="stop"
          label="⏹ Stop"
          onPress={async () => {
            await run(['--cancel'])
            await update($, isPaused, () => false)
          }}
        />
      </Box>
    )
  })
}
