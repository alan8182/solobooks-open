/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export type WorkspaceView = 'boss' | 'accounting'

export const WORKSPACE_VIEWS: WorkspaceView[] = ['boss', 'accounting']

export function isWorkspaceView(value: unknown): value is WorkspaceView {
  return value === 'boss' || value === 'accounting'
}

export function itemVisible(
  workspace: WorkspaceView[] | undefined,
  mode: WorkspaceView,
): boolean {
  if (!workspace || workspace.length === 0) return true
  return workspace.includes(mode)
}

export function routeAllowsWorkspace(
  workspace: unknown,
  mode: WorkspaceView,
): boolean {
  if (!Array.isArray(workspace) || workspace.length === 0) return true
  return workspace.includes(mode)
}
