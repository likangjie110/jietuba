#!/usr/bin/env bash
# 一次性准备「本机代码签名身份」，让打包出来的 Jietuba.app 有稳定签名。
#
# 为什么需要它：原来的打包是 ad-hoc 签名（codesign -s -），它的 designated
# requirement 是 cdhash ——「按内容哈希识别」。macOS 的隐私授权库（TCC）记的就是这份
# requirement，于是每次重新打包、内容一变，屏幕录制与辅助功能里的旧授权就对不上：
# 表现为每次更新都要重新授权，有时还得先把系统设置里的旧条目删掉再加回来。
#
# 换成自签名证书后，designated requirement 变成
#   identifier "com.jietuba.app" and certificate leaf = H"<证书哈希>"
# 只要证书不变，重新打包多少次都仍匹配，授权给一次就长期有效。
#
# 三个非显然的坑（本机 macOS 26 实测）：
#   1) 身份放进登录 keychain 会弹系统授权框：codesign 实际由
#      com.apple.CodeSigningHelper 代签，ACL 里的 codesign 条目拦不住它，而改
#      partition list 又必须先知道登录 keychain 密码。所以这里单独建一个 keychain，
#      partition list 显式设成只有 Apple 签名的工具能用（比 -A「允许任意程序」安全）。
#   2) codesign 只认「受信任」的身份：自签名证书默认是 CSSMERR_TP_NOT_TRUSTED，
#      表现成 no identity found，必须把它加到 codeSign 信任策略。
#   3) codesign 的 --keychain 参数在 macOS 26 上找不到身份（实测 no identity found），
#      必须让这个 keychain 出现在用户 keychain 搜索列表里，且要排在登录 keychain
#      前面（登录 keychain 里若也有同名身份，排后面就抢不到，也就不会弹框）。
#
# 用法：
#   ./setup_macos_signing.sh
#
# 首次运行会在设置信任时弹一次系统授权框（macOS 的硬要求，改信任必须过一次授权），
# 输入登录密码允许一次即可；之后再跑这个脚本只是检查，不会重复弹。
#
# 卸载（恢复搜索列表并删掉 keychain；打包脚本会退回报错，除非改环境变量）：
#   security list-keychains -d user -s "$HOME/Library/Keychains/login.keychain-db"
#   security delete-keychain "$HOME/Library/Keychains/jietuba-signing.keychain-db"
set -euo pipefail

IDENTITY="${JIETUBA_CODESIGN_IDENTITY:-Jietuba Local Signing}"
KEYCHAIN="${JIETUBA_SIGNING_KEYCHAIN:-$HOME/Library/Keychains/jietuba-signing.keychain-db}"
LOGIN_KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== 1/4 准备签名证书（CN=${IDENTITY}） =="
# 判断标准是「这个 keychain 里的身份可用」：只看 find-identity -v 会误判 —— 登录
# keychain 里可能有同名副本，而它的私钥每次使用都会弹授权框（见文件头的坑 1）
if security find-identity -v -p codesigning "$KEYCHAIN" 2>/dev/null | grep -qF "\"$IDENTITY\""; then
    echo "身份已就绪，跳过创建与信任设置"
else
    if security find-certificate -c "$IDENTITY" "$KEYCHAIN" >/dev/null 2>&1; then
        echo "已有同名证书，只补信任设置"
        security find-certificate -c "$IDENTITY" -p "$KEYCHAIN" >"$WORK/cert.pem"
    else
        # 自签名、只做代码签名用途的证书
        openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
            -keyout "$WORK/key.pem" -out "$WORK/cert.pem" \
            -subj "/CN=$IDENTITY" \
            -addext "basicConstraints=critical,CA:FALSE" \
            -addext "keyUsage=critical,digitalSignature" \
            -addext "extendedKeyUsage=critical,codeSigning"
        # 必须用传统 PKCS#12 算法：security 认不出 OpenSSL 3 默认的 PBES2
        # （报 MAC verification failed during PKCS12 import）
        openssl pkcs12 -export -out "$WORK/cert.p12" -inkey "$WORK/key.pem" -in "$WORK/cert.pem" \
            -certpbe PBE-SHA1-3DES -keypbe PBE-SHA1-3DES -macalg sha1 -passout pass:jietuba
        security create-keychain -p "" "$KEYCHAIN"
        security import "$WORK/cert.p12" -k "$KEYCHAIN" -P jietuba -T /usr/bin/codesign
        # 关键一步：私钥只允许 Apple 签名的签名工具使用（含 codesign 的代签进程），
        # 这样 codesign 用它是静默的，不会弹授权框。空密码 keychain 才能非交互设置它。
        security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "" "$KEYCHAIN" >/dev/null
    fi

    # 自签名证书默认不受信，不加这一步 codesign 会拒绝使用该身份（no identity found）。
    # macOS 要求改信任设置必须过一次用户授权，所以这里会弹一次系统对话框（只需这一次）。
    echo ">>> 接下来会弹一次系统授权框（改信任设置需要），输入登录密码允许一次"
    security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" "$WORK/cert.pem"
fi
echo "证书就绪：$(security find-certificate -c "$IDENTITY" -Z "$KEYCHAIN" | awk '/SHA-1/{print $3}')"

echo "== 2/4 把 keychain 放进用户搜索列表（第一位） =="
# security list-keychains 打印的格式不适合原样回传（可能带 " -db" 之类后缀），
# 这里只保留真实存在的路径，并始终确保登录 keychain 在列表里
existing=()
while IFS= read -r line; do
    path="${line#"${line%%[![:space:]]*}"}"
    path="${path%\"}"
    path="${path#\"}"
    [ -e "$path" ] || continue
    existing+=("$path")
done < <(security list-keychains -d user)

ordered=("$KEYCHAIN" "$LOGIN_KEYCHAIN")
for path in "${existing[@]}"; do
    skip=0
    for known in "${ordered[@]}"; do
        [ "$path" = "$known" ] && skip=1
    done
    [ "$skip" = 1 ] || ordered+=("$path")
done
security list-keychains -d user -s "${ordered[@]}"
security list-keychains -d user

echo "== 3/4 解锁 keychain =="
# 自建 keychain 默认空闲 5 分钟 / 睡眠后上锁。改锁定时长（set-keychain-settings）同样要
# 过一次授权，不值得，所以不去动它：打包脚本每次签名前会自己解一次（空密码，静默）。
security unlock-keychain -p "" "$KEYCHAIN"

echo "== 4/4 验证：签一个临时副本，看 requirement 是否按证书识别 =="
cp /bin/echo "$WORK/probe"
codesign --force --timestamp=none --sign "$IDENTITY" "$WORK/probe"
requirement="$(codesign -d -r- "$WORK/probe" 2>&1 | sed -n 's/^designated => //p')"
case "$requirement" in
    *"certificate leaf"*) echo "OK：$requirement" ;;
    *)
        echo "签名没有生效，requirement 是：$requirement" >&2
        exit 1
        ;;
esac

cat <<EOF

签名身份就绪：$IDENTITY

重新打包（会自动用它签名，不再需要任何授权点击）：
    venv311/bin/python build_macos_app.py

注意：这是第一次从 ad-hoc 签名切过来，TCC 里旧的 jietuba 条目是按内容哈希记的，
对不上新签名。给新包重新授权一次（系统设置里旧的重复条目可以删掉）：
    tccutil reset Accessibility com.jietuba.app
    tccutil reset ScreenCapture com.jietuba.app
之后再重新打包，授权都不会失效。
EOF
