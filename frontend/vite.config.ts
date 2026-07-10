import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'react-vendor',
              test: /node_modules\/(react|react-dom|react-router|react-router-dom)\//,
            },
            {
              name: 'antd-icons',
              test: /node_modules\/(@ant-design\/icons|@ant-design\/icons-svg)\//,
            },
            {
              name: 'antd-core',
              test: /node_modules\/(antd|@ant-design\/cssinjs|@ant-design\/colors)\//,
            },
            {
              name: 'rc-vendor',
              test: /node_modules\/(@rc-component|rc-[^/]+)\//,
            },
            {
              name: 'vendor',
              test: /node_modules\//,
            },
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
