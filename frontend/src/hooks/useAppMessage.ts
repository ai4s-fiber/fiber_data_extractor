/**
 * Re-export Ant Design's `useApp()` hook for convenient access.
 *
 * Usage:
 *   import { useAppMessage } from '../hooks/useAppMessage';
 *   const { message, notification, modal } = useAppMessage();
 *
 * This avoids the console warning:
 *   "Static function can not consume context like dynamic theme.
 *    Please use 'App' component instead."
 */
import { App } from 'antd';

export function useAppMessage() {
  return App.useApp();
}
