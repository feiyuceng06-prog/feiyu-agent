//! 一条给外面来的路径用的闸：**必须落在工作区里**。
//!
//! 两个二进制都用它（桌面端的 `read_image`、devserver 的 `/file`），所以放这儿
//! 一份、测一份 —— 安全检查没有测试等于没有检查。
//!
//! 路径的来源是 agent 的输出和查询串，而 agent 会读网页、会跑用户给的命令，
//! 不是可信来源。`canonicalize` 之后比前缀：`..` 和符号链接都在这一步被摊平，
//! 所以指到 `~/.ssh/id_rsa` 的软链也过不去。
//!
//! `Path::starts_with` 是**按路径分量**比的，不是按字符串前缀 ——
//! 所以 `/home/a/ws-secrets` 不会被当成 `/home/a/ws` 的子路径。

use std::path::{Path, PathBuf};

pub fn under_root(root: &Path, want: &str) -> Option<PathBuf> {
    let root = std::fs::canonicalize(root).ok()?;
    let target = std::fs::canonicalize(root.join(want)).ok()?;
    target.starts_with(&root).then_some(target)
}

#[cfg(test)]
mod tests {
    use super::under_root;

    fn bed() -> tempdir::Dir {
        tempdir::Dir::new()
    }

    #[test]
    fn 工作区里的文件放行() {
        let dir = bed();
        std::fs::write(dir.path().join("a.png"), b"x").unwrap();
        assert!(under_root(dir.path(), "a.png").is_some());
        std::fs::create_dir(dir.path().join("sub")).unwrap();
        std::fs::write(dir.path().join("sub/b.png"), b"x").unwrap();
        assert!(under_root(dir.path(), "sub/b.png").is_some());
    }

    #[test]
    fn 点点越界拦下() {
        let dir = bed();
        std::fs::write(dir.path().parent().unwrap().join("outside.png"), b"x").unwrap();
        assert!(under_root(dir.path(), "../outside.png").is_none());
        assert!(under_root(dir.path(), "../../../../etc/passwd").is_none());
    }

    #[test]
    fn 绝对路径拦下() {
        // Path::join 碰到绝对路径会**整个替换**掉前面那截，不是拼接
        let dir = bed();
        assert!(under_root(dir.path(), "/etc/passwd").is_none());
    }

    #[test]
    fn 指到外面的软链拦下() {
        let dir = bed();
        let outside = dir.path().parent().unwrap().join("secret.png");
        std::fs::write(&outside, b"x").unwrap();
        std::os::unix::fs::symlink(&outside, dir.path().join("link.png")).unwrap();
        assert!(under_root(dir.path(), "link.png").is_none());
    }

    #[test]
    fn 名字撞前缀的兄弟目录不算子路径() {
        let dir = bed();
        let ws = dir.path().join("ws");
        let sibling = dir.path().join("ws-secrets");
        std::fs::create_dir(&ws).unwrap();
        std::fs::create_dir(&sibling).unwrap();
        std::fs::write(sibling.join("k.png"), b"x").unwrap();
        assert!(under_root(&ws, "../ws-secrets/k.png").is_none());
    }

    #[test]
    fn 不存在的文件回_none() {
        assert!(under_root(bed().path(), "nope.png").is_none());
    }

    /// 不引 tempfile 那个 crate —— 这棵树里就没有它，为了六条用例不值得加一个依赖。
    mod tempdir {
        use std::path::{Path, PathBuf};

        pub struct Dir(PathBuf);

        impl Dir {
            pub fn new() -> Self {
                let base = std::env::temp_dir().join(format!(
                    "feiyu-pathguard-{}-{:?}",
                    std::process::id(),
                    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap()
                ));
                let here = base.join("ws");
                std::fs::create_dir_all(&here).unwrap();
                Dir(here)
            }

            pub fn path(&self) -> &Path {
                &self.0
            }
        }

        impl Drop for Dir {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(self.0.parent().unwrap());
            }
        }
    }
}
