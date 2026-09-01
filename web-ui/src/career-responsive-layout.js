export function getCareerLayoutMode(width) {
  const normalizedWidth = Number(width) || 0
  if (normalizedWidth < 820) return 'single'
  if (normalizedWidth < 1240) return 'compact'
  return 'wide'
}

export function getPanelAfterLayoutChange(previousMode, nextMode, panel) {
  return previousMode === nextMode ? panel : ''
}
