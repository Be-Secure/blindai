/*
 * Copyright (C) 2011-2021 Intel Corporation. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 *   * Redistributions of source code must retain the above copyright
 *     notice, this list of conditions and the following disclaimer.
 *   * Redistributions in binary form must reproduce the above copyright
 *     notice, this list of conditions and the following disclaimer in
 *     the documentation and/or other materials provided with the
 *     distribution.
 *   * Neither the name of Intel Corporation nor the names of its
 *     contributors may be used to endorse or promote products derived
 *     from this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 *
 */

#ifndef SGXECDSAATTESTATION_IATTESTATIONLIBRARYADAPTER_H
#define SGXECDSAATTESTATION_IATTESTATIONLIBRARYADAPTER_H

#include <string>
#include <vector>
#include <ctime>


#include "../../../AttestationLibrary/include/SgxEcdsaAttestation/QuoteVerification.h"

namespace intel { namespace sgx { namespace dcap {

struct IAttestationLibraryAdapter
{
    virtual  ~IAttestationLibraryAdapter() = default;

    virtual std::string getVersion() const = 0;

    virtual Status verifyQuote(const std::vector<uint8_t>& quote,
                               const std::string& pckCertChain,
                               const std::string& pckCrl,
                               const std::string& tcbInfo,
                               const std::string& qeIdentity) const = 0;

    virtual Status verifyPCKCertificate(const std::string& pemCertChain,
                                        const std::string& pemRootCaCRL,
                                        const std::string& intermediateCaCRL,
                                        const std::string& pemRootCaCertificate,
                                        const time_t& expirationDate) const = 0;

    virtual Status verifyTCBInfo(const std::string& tcbInfo,
                                 const std::string& pemSigningChain,
                                 const std::string& pemRootCaCrl,
                                 const std::string& pemtrustedRootCaCertificate,
                                 const time_t& expirationDate) const = 0;

    virtual Status verifyQeIdentity(const std::string& qeIdentity,
                                    const std::string& pemSigningChain,
                                    const std::string& pemRootCaCrl,
                                    const std::string& pemtrustedRootCaCertificate,
                                    const time_t& expirationDate) const = 0;
};

}}}
#endif //SGXECDSAATTESTATION_IATTESTATIONLIBRARYADAPTER_H
