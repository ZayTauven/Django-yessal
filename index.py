import pusher

pusher_client = pusher.Pusher(
  app_id='2157369',
  key='0369577c46baf67bfc0a',
  secret='2f6edb42cbc7120b33cb',
  cluster='eu',
  ssl=True
)

pusher_client.trigger('my-channel', 'my-event', {'message': 'hello world'})
