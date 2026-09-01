export function modelProfilesFromAvailability(payload) {
  if (!Array.isArray(payload?.items)) return []
  return payload.items
    .map((item) => item?.profile)
    .filter((profile) => typeof profile?.profile_key === 'string' && profile.profile_key.length > 0)
}
