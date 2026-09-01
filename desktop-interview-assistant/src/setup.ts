export interface SessionRequest {
  asr_model_profile_id: string | null
  answer_model_profile_id: string | null
}

export function buildSessionRequest(asrModelId: string, answerModelId: string): SessionRequest {
  return {
    asr_model_profile_id: asrModelId || null,
    answer_model_profile_id: answerModelId || null,
  }
}
