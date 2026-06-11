import axios from 'axios'

const http = axios.create({ baseURL: '/api' })

export const api = {
  runWorkflow: (contacts) =>
    http.post('/workflow/run', { contacts }),

  runWorkflowFromFile: (file) => {
    const form = new FormData()
    form.append('file', file)
    return axios.post('/api/workflow/run-file', form)
  },

  getWorkflow: (threadId) =>
    http.get(`/workflow/${threadId}`),

  getTrace: (threadId) =>
    http.get(`/workflow/${threadId}/trace`),

  listWorkflows: () =>
    http.get('/workflow/'),

  reviewWorkflow: (threadId, action, editedGifts = null, feedback = null, tone = 'professional') =>
    http.post(`/workflow/${threadId}/review`, {
      action,
      edited_gifts: editedGifts,
      feedback,
      tone,
    }),
}
