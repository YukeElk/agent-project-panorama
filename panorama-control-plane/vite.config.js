import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  root: 'src/workbench',
  plugins: [react()],
  build: {
    outDir: '../../dist/workbench',
    emptyOutDir: true,
    sourcemap: true,
  },
});
