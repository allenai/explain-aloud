export type Paused = boolean
export type Status = { phase: '' | 'generating' | 'speaking'; text: string }
export type Target = number

declare module 'claude-code' {
  interface PluginState {
    'explain-aloud': { isPaused: Paused; status: Status; target: Target }
  }
}
