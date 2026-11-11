export type Paused = boolean

declare module 'claude-code' {
  interface PluginState {
    'explain-aloud': { isPaused: Paused }
  }
}
