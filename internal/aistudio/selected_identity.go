package aistudio

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"google.golang.org/protobuf/encoding/protowire"
)

const selectedAccountsURL = "https://accounts.google.com/ListAccounts?gpsia=1&source=ChromiumBrowser&json=standard&laf=b64bin"

// SelectedIdentity is an account selector, never a subscription claim.
type SelectedIdentity struct {
	Email    string `json:"email"`
	GaiaID   string `json:"gaia_id"`
	AuthUser int    `json:"authuser"`
}

type selectedAccount struct {
	Email, GaiaID              string
	Valid, SignedOut, Verified bool
}

func (s StorageState) SelectedIdentity() (*SelectedIdentity, error) {
	raw, ok := s.extra["xiaoguofan_identity"]
	if !ok {
		return nil, nil
	}
	var id SelectedIdentity
	if err := json.Unmarshal(raw, &id); err != nil {
		return nil, errors.New("账号选择信息格式无效，请重新导出")
	}
	if id.AuthUser < 0 || id.AuthUser > 99 || !strings.Contains(id.Email, "@") || len(id.Email) > 254 || len(id.GaiaID) < 1 || len(id.GaiaID) > 32 {
		return nil, errors.New("账号选择信息无效，请重新导出")
	}
	for _, c := range id.GaiaID {
		if c < '0' || c > '9' {
			return nil, errors.New("Google 身份标识无效，请重新导出")
		}
	}
	return &id, nil
}

func decodeSelectedAccounts(raw []byte) ([]selectedAccount, error) {
	text := strings.TrimSpace(string(raw))
	var quoted string
	if json.Unmarshal([]byte(text), &quoted) == nil {
		text = quoted
	}
	data, err := base64.StdEncoding.DecodeString(text)
	if err != nil {
		data, err = base64.RawStdEncoding.DecodeString(text)
	}
	if err != nil {
		return nil, errors.New("无法解析 Google 账号列表，请重新导出")
	}
	rows := []selectedAccount{}
	for len(data) > 0 {
		num, typ, n := protowire.ConsumeTag(data)
		if n < 0 {
			return nil, errors.New("无效账号列表")
		}
		data = data[n:]
		if num != 1 || typ != protowire.BytesType {
			return nil, errors.New("账号列表协议已变化")
		}
		value, n := protowire.ConsumeBytes(data)
		if n < 0 {
			return nil, errors.New("无效账号列表")
		}
		data = data[n:]
		row := selectedAccount{Valid: true, Verified: true}
		for len(value) > 0 {
			field, wire, k := protowire.ConsumeTag(value)
			if k < 0 {
				return nil, errors.New("无效账号字段")
			}
			value = value[k:]
			switch {
			case (field == 3 || field == 10) && wire == protowire.BytesType:
				v, k := protowire.ConsumeBytes(value)
				if k < 0 {
					return nil, errors.New("无效账号字段")
				}
				value = value[k:]
				if field == 3 {
					row.Email = strings.ToLower(string(v))
				} else {
					row.GaiaID = string(v)
				}
			case (field == 9 || field == 14 || field == 15) && wire == protowire.VarintType:
				v, k := protowire.ConsumeVarint(value)
				if k < 0 {
					return nil, errors.New("无效账号状态")
				}
				value = value[k:]
				if field == 9 {
					row.Valid = v != 0
				}
				if field == 14 {
					row.SignedOut = v != 0
				}
				if field == 15 {
					row.Verified = v != 0
				}
			default:
				k := protowire.ConsumeFieldValue(field, wire, value)
				if k < 0 {
					return nil, errors.New("无效账号字段")
				}
				value = value[k:]
			}
		}
		rows = append(rows, row)
		if len(rows) > 100 {
			return nil, errors.New("账号列表过长")
		}
	}
	return rows, nil
}

func matchSelectedIdentity(rows []selectedAccount, id SelectedIdentity) (string, error) {
	index := -1
	for i, row := range rows {
		if row.GaiaID == id.GaiaID && strings.EqualFold(row.Email, id.Email) && row.Valid && !row.SignedOut && row.Verified {
			if index >= 0 {
				return "", errors.New("Google 身份不唯一")
			}
			index = i
		}
	}
	if index < 0 {
		return "", errors.New("登录会话与所选 Google 账号不匹配，请重新导出")
	}
	return strconv.Itoa(index), nil
}

// ResolveSelectedAuthUser verifies email+Gaia against Google's current cookie session.
// Never trust the saved numeric position: account order can change.
func ResolveSelectedAuthUser(ctx context.Context, client *http.Client, state StorageState) (string, error) {
	id, err := state.SelectedIdentity()
	if err != nil {
		return "", err
	}
	if id == nil {
		return "0", nil
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, selectedAccountsURL, strings.NewReader(" "))
	if err != nil {
		return "", err
	}
	cookie, err := state.CookieHeader(selectedAccountsURL, time.Now())
	if err != nil {
		return "", err
	}
	req.Header.Set("Cookie", cookie)
	req.Header.Set("User-Agent", publicDiscoveryUserAgent)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Origin", "https://www.google.com")
	req.Header.Set("Referer", aiStudioOrigin+"/")
	isolated := *client
	isolated.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	res, err := isolated.Do(req)
	if err != nil {
		return "", errors.New("无法核对 Google 登录身份，请稍后重试")
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return "", fmt.Errorf("Google 身份核对返回 HTTP %d，请重新导出", res.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(res.Body, 100001))
	if err != nil || len(raw) > 100000 {
		return "", errors.New("Google 身份响应无效")
	}
	rows, err := decodeSelectedAccounts(raw)
	if err != nil {
		return "", err
	}
	if len(rows) == 0 {
		return resolveMyAccountDefault(ctx, &isolated, state, *id)
	}
	return matchSelectedIdentity(rows, *id)
}
