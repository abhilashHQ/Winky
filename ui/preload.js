const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('winky', {
  onHarnessEvent: (cb) => {
    if (typeof cb !== 'function') {
      return () => {}
    }

    const listener = (_, msg) => cb(msg)
    ipcRenderer.on('harness-event', listener)

    return () => {
      ipcRenderer.removeListener('harness-event', listener)
    }
  }
})
