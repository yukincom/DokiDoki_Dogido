//! Own only the background watcher, never the monitored operation's cleanup.
use std::future::Future;
use tokio::task::JoinHandle;

pub(super) struct Monitor(Option<JoinHandle<()>>);

impl Monitor {
    pub fn spawn(watcher: impl Future<Output = ()> + Send + 'static) -> Self {
        Self(Some(tokio::spawn(watcher)))
    }

    /// Normal exits wait until the watcher releases its captured session owner.
    pub async fn finish(mut self) {
        if let Some(task) = self.0.take() {
            task.abort();
            let _ = task.await;
        }
    }
}

impl Drop for Monitor {
    fn drop(&mut self) {
        // A panic or a dropped parent must not detach an endless watcher. Drop
        // cannot await; explicit finish remains the normal completion boundary.
        if let Some(task) = &self.0 {
            task.abort();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::oneshot;

    struct Released(Option<oneshot::Sender<()>>);
    impl Drop for Released {
        fn drop(&mut self) {
            let _ = self.0.take().unwrap().send(());
        }
    }

    async fn watcher() -> (Monitor, oneshot::Receiver<()>) {
        let (ready, started) = oneshot::channel();
        let (released, receiver) = oneshot::channel();
        let monitor = Monitor::spawn(async move {
            let _release = Released(Some(released));
            let _ = ready.send(());
            std::future::pending::<()>().await;
        });
        started.await.unwrap();
        (monitor, receiver)
    }

    #[tokio::test]
    async fn normal_finish_joins_the_watcher_and_releases_its_owner() {
        let (monitor, mut released) = watcher().await;
        monitor.finish().await;
        assert_eq!(released.try_recv(), Ok(()));
    }

    #[tokio::test]
    async fn dropped_parent_does_not_leave_a_detached_watcher() {
        let (ready, started) = oneshot::channel();
        let (released, receiver) = oneshot::channel();
        let parent = tokio::spawn(async move {
            let (_monitor, done) = watcher().await;
            ready.send(done).unwrap();
            let _release = Released(Some(released));
            std::future::pending::<()>().await;
        });
        let watcher_released = started.await.unwrap();
        parent.abort();
        let _ = parent.await;
        receiver.await.unwrap();
        tokio::time::timeout(std::time::Duration::from_secs(1), watcher_released)
            .await
            .unwrap()
            .unwrap();
    }
}
