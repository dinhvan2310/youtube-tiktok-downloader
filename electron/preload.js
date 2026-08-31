const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('desktop', {
  chooseDirectory: () => ipcRenderer.invoke('choose-directory'),
  chooseFile: filters => ipcRenderer.invoke('choose-file', filters || [{ name: 'All files', extensions: ['*'] }]),
  saveFile: options => ipcRenderer.invoke('save-file', options || {}),
  openPath: p => ipcRenderer.invoke('open-path', p),
  notify: payload => ipcRenderer.invoke('notify', payload)
});
