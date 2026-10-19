/* JSON work runs off the UI thread. Data format is unchanged. */
self.onmessage = ({ data: { id, operation, value } }) => {
  try {
    const result = operation === 'encode' ? JSON.stringify(value) : JSON.parse(value);
    self.postMessage({ id, result });
  } catch (error) {
    self.postMessage({ id, error: error.message });
  }
};
