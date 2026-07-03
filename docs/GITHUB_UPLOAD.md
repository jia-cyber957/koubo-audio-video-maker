# 第一次上传 GitHub 指南

如果你以前只在 GitHub 下载过东西，推荐先用网页上传法。它最直观，不需要先学 Git 命令。

## 上传前检查

只上传这个文件夹里的内容：

```text
github-release/koubo-audio-video-maker
```

不要上传你的测试音频、API key、`.env`、生成的视频素材、`outputs`、`work` 目录。

## 仓库名建议

推荐：

```text
koubo-audio-video-maker
```

也可以：

```text
low-cost-koubo-video-maker
ai-koubo-video-workflow
```

不建议把仓库名写成：

```text
huasheng-ai-lite
huasheng-ai-youth
huasheng-ai-free
花生AI青春版
```

原因：这种名字太像官方轻量版，容易被误会成和花生AI有授权、合作或从属关系。

## About 怎么填

Description：

```text
Low-cost AI workflow for Chinese narration videos: audio transcription, semantic storyboards, stock-video search, LLM scoring, resumable downloads, and MP4 assembly.
```

Website：

```text
可以先留空
```

Topics：

```text
codex-skill
ai-video
narration-video
chinese-video
stock-video
pexels
pixabay
volcengine
opencode
video-assembly
```

## 方法一：网页上传

1. 打开 GitHub。
2. 点右上角 `+`。
3. 选择 `New repository`。
4. Repository name 填：

```text
koubo-audio-video-maker
```

5. Description 填上面那段 About。
6. 选择 `Public`。
7. 不要勾选 `Add a README file`，因为我们已经写好了 README。
8. 创建仓库。
9. 点 `uploading an existing file`。
10. 把 `github-release/koubo-audio-video-maker` 文件夹里的所有内容拖进去。
11. Commit message 写：

```text
Initial release
```

12. 点 `Commit changes`。

## 方法二：Git 命令上传

进入发布目录：

```powershell
cd "C:\Users\Karl\Desktop\新建文件夹\github-release\koubo-audio-video-maker"
```

初始化并提交：

```powershell
git init
git add .
git commit -m "Initial release"
git branch -M main
git remote add origin https://github.com/<你的用户名>/koubo-audio-video-maker.git
git push -u origin main
```

如果 GitHub 要求登录，按它提示走浏览器登录或 token。

## Release 怎么发

仓库上传后，可以点右侧 `Releases`，创建第一个版本：

Tag：

```text
v0.1.0
```

Title：

```text
v0.1.0 - Low-cost AI narration video workflow
```

Release notes：

```text
第一个公开版本：

- 单音频入口
- 火山引擎转写和字级时间轴
- 10 秒内 AI 语义分镜
- Pexels/Pixabay 多轮素材搜索
- LLM/Codex 文本评分
- 无视觉审核
- 下载可恢复，已下载 MP4 自动跳过
- MP4 装配导出
```

可以把本地打包文件也传到 Release 附件里：

```text
C:\Users\Karl\Desktop\新建文件夹\过程\打包\koubo-audio-video-maker-standalone-clean-20260703-105611.zip
```

## 发布时可以怎么说

可以说：

```text
花生AI太贵？我做了一个开源低成本口播配画面工作流。
```

不要说：

```text
花生AI青春版
花生AI开源版
花生AI免费版
```

前者是成本对比和创作动机，后者容易让人误会你和花生AI有官方关系。
